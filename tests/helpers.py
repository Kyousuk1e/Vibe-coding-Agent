import copy

from agent.jsonutil import dumps


def final(answer="完成"):
    return {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": answer}}]}


def call(name, args, call_id="call_1"):
    return {"id": call_id, "type": "function", "function": {
        "name": name, "arguments": args if isinstance(args, str) else dumps(args)}}


def tool(name, args, call_id="call_1"):
    return tool_calls(call(name, args, call_id))


def tool_calls(*calls):
    return {"choices": [{"finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": None, "tool_calls": list(calls)}}]}


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    async def complete(self, *, messages, tools):
        self.requests.append(copy.deepcopy({"messages": messages, "tools": tools}))
        if not self.replies:
            raise AssertionError("Unexpected extra model call")
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        if callable(reply):
            reply = reply(messages, tools)
        return copy.deepcopy(reply)
