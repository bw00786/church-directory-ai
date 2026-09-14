from collections import Counter


class VoiceMetrics:
    def __init__(self):
        self.values = Counter()

    def add(self, key: str, amount: float = 1):
        self.values[key] += amount

    def snapshot(self) -> dict:
        values = dict(self.values)
        total = max(1, self.values["attention_events"])
        values["operator_acknowledgement_rate"] = self.values["acknowledgements"] / total
        values["operator_dismiss_rate"] = self.values["dismissals"] / total
        values["average_attention_response_time"] = self.values["response_seconds"] / max(1, self.values["responses"])
        return values