from rag import reranked_retrieve


def test(query, kind=None, n=5):
    print("\n" + "=" * 60)
    print(f"QUERY: {query}")
    print(f"KIND:  {kind}")
    print("=" * 60)

    results = reranked_retrieve(query)

    if not results:
        print("❌ No memories found")
        return

    for i, result in enumerate(results, 1):
        print(f"\n{i}. {result}")


while True:
    query = input("\nYou: ")

    if query.lower() in ["exit", "quit"]:
        break

    test(query)
