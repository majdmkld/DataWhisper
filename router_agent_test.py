import json
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

PROJECT_ENDPOINT = "YOUR_AI_FOUNDRY_PROJECT_ENDPOINT"

credential = DefaultAzureCredential()
project = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential)
openai = project.get_openai_client()


def route_and_answer(user_query: str) -> str:
    """Send a query to RouterAgent, detect the routing decision, and forward to the target agent."""

    # Step 1: Ask RouterAgent to classify the query
    router_response = openai.responses.create(
        input=user_query,
        extra_body={"agent_reference": {"name": "RouterAgent", "type": "agent_reference"}},
    )

    # Step 2: Find the function call in the response
    target_agent = None
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
        # Fallback: RouterAgent replied with text instead of a function call
        return f"[RouterAgent replied directly]: {router_response.output_text}"

    print(f"  -> Routed to: {target_agent}")

    # Step 3: Forward the query to the target agent
    final_response = openai.responses.create(
        input=routed_query,
        extra_body={"agent_reference": {"name": target_agent, "type": "agent_reference"}},
    )

    return final_response.output_text


if __name__ == "__main__":
    test_queries = [
        "How many employees are in the Engineering department?",
        "What were the total sales last quarter?",
        "Who is the manager of the Marketing department?",
        "Which product has the highest revenue?",
    ]

    for query in test_queries:
        print(f"\nQ: {query}")
        answer = route_and_answer(query)
        print(f"A: {answer}")
