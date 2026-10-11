import pytest


@pytest.mark.parametrize("query,expected", [
    ("How long do I have to return something?", "returns-and-refunds.md#Return window"),
    ("When will I get my refund?", "returns-and-refunds.md#Refund timing"),
    ("Do you ship to Japan?", "shipping.md#International shipping"),
    ("How much is express delivery?", "shipping.md#Express delivery"),
    ("Can I cancel my order after it shipped?", "orders-and-payments.md#Cancelling an order"),
    ("What is the warranty on tents?", "warranty.md#Warranty period"),
    ("Which payment methods do you accept?", "orders-and-payments.md#Payment methods"),
])
def test_top_hit(retriever, query, expected):
    hits = retriever.search(query)
    assert hits and hits[0].chunk.source == expected


def test_unrelated_query_has_no_strong_hit(retriever):
    hits = retriever.search("What's the weather in Busan tomorrow?")
    assert not hits or hits[0].score < 2.0
