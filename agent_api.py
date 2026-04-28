import json
import os
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agent_api")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5050"],
    allow_methods=["POST"],
    allow_headers=["Content-Type"],
)

PROJECT_ENDPOINT = "YOUR_AI_FOUNDRY_PROJECT_ENDPOINT"
FOUNDRY_DEPLOYMENT = os.getenv("FOUNDRY_DEPLOYMENT", "gpt-4o")

credential = DefaultAzureCredential()
project = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential)
openai = project.get_openai_client()

EXTRACT_CHART_PROMPT = """\
You are a data-extraction assistant. Given a text answer from an AI agent, \
extract any structured data that can be visualised in a chart.

### Rules
- Look for lists, rankings, counts, percentages, comparisons, or trends in the text.
- If the text contains chartable data, return a JSON object with the fields below.
- If no chartable data exists (e.g. the answer is purely conversational), return: \
{"chartable": false}
- The "data" array must contain objects with consistent keys matching \
"label_column" and each entry in "value_columns".
- Keep numbers numeric (not strings).
- Pick the most appropriate chart_type for the data shape.

### Response format
Return ONLY a JSON object — no markdown fences, no extra text:
{
  "chartable": true,
  "data": [{ "Category": "A", "Count": 10 }, ...],
  "chart_type": "bar | horizontalBar | line | pie | doughnut | table | kpi | stackedBar",
  "label_column": "Category",
  "value_columns": ["Count"],
  "title": "Short chart title"
}
"""


class ChatRequest(BaseModel):
    message: str
    active_filters: list[dict] | None = None


class ChatResponse(BaseModel):
    answer: str
    routed_to: str | None = None
    chart_type: str | None = None
    data: list | None = None
    label_column: str | None = None
    value_columns: list | None = None
    title: str | None = None


def build_filter_context(filters: list[dict]) -> str:
    """Convert dashboard filter objects into a natural-language constraint for the agent."""
    if not filters:
        return ""
    lines = []
    for f in filters:
        table = f.get("table", "")
        column = f.get("column", "")
        values = f.get("values", [])
        operator = f.get("operator", "In")
        if values:
            vals_str = ", ".join(str(v) for v in values)
            lines.append(f"  - {table}.{column} {operator} [{vals_str}]")
        elif f.get("conditions"):
            lines.append(f"  - {table}.{column} has advanced filter conditions: {f['conditions']}")
    if not lines:
        return ""
    header = (
        "ACTIVE DASHBOARD FILTERS — You MUST restrict your answer to ONLY data "
        "matching ALL of the following filters. Do NOT include data outside this scope:\n"
    )
    return header + "\n".join(lines)


def route_and_answer(user_query: str, filters: list[dict] | None = None) -> tuple[str, str | None]:
    router_response = openai.responses.create(
        input=user_query,
        extra_body={"agent_reference": {"name": "RouterAgent", "type": "agent_reference"}},
    )

    target_agent = None
    routed_query = user_query
    for item in router_response.output:
        if item.type == "function_call":
            if item.name == "route_to_hr_agent":
                target_agent = "HRAgent"
            elif item.name == "route_to_sales_agent":
                target_agent = "SalesAgent"
            args = json.loads(item.arguments)
            routed_query = args.get("query", user_query)
            break

    if target_agent is None:
        return (f"{router_response.output_text}", None)

    # Prepend filter context so the agent restricts its answer
    filter_context = build_filter_context(filters) if filters else ""
    if filter_context:
        routed_query = f"{filter_context}\n\nUser question: {routed_query}"

    final_response = openai.responses.create(
        input=routed_query,
        extra_body={"agent_reference": {"name": target_agent, "type": "agent_reference"}},
    )

    return (final_response.output_text, target_agent)


def extract_chart(agent_answer: str) -> dict | None:
    """Ask the LLM to pull structured chart data out of the agent's text."""
    try:
        r = openai.chat.completions.create(
            model=FOUNDRY_DEPLOYMENT,
            messages=[
                {"role": "system", "content": EXTRACT_CHART_PROMPT},
                {"role": "user", "content": agent_answer},
            ],
            temperature=0,
            max_completion_tokens=2000,
            response_format={"type": "json_object"},
        )
        content = r.choices[0].message.content
        result = json.loads(content)
        if result.get("chartable") and result.get("data"):
            return result
        return None
    except Exception as exc:
        logger.info("Chart extraction skipped: %s", exc)
        return None


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    answer, routed_to = route_and_answer(req.message, req.active_filters)

    chart_info = extract_chart(answer)

    if chart_info:
        return ChatResponse(
            answer=answer,
            routed_to=routed_to,
            chart_type=chart_info.get("chart_type"),
            data=chart_info.get("data"),
            label_column=chart_info.get("label_column"),
            value_columns=chart_info.get("value_columns"),
            title=chart_info.get("title"),
        )

    return ChatResponse(answer=answer, routed_to=routed_to)
