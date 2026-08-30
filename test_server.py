import json
import os
import unittest
from unittest import mock

os.environ.setdefault("FEISHU_APP_ID", "app")
os.environ.setdefault("FEISHU_APP_SECRET", "secret")
os.environ.setdefault("FEISHU_VERIFICATION_TOKEN", "token")
os.environ.setdefault("OPENAI_API_KEY", "key")

import server


class ServerTests(unittest.TestCase):
    def test_duplicate_detection(self):
        server._seen.clear()
        self.assertFalse(server.is_duplicate("evt-1"))
        self.assertTrue(server.is_duplicate("evt-1"))

    @mock.patch.object(server, "reply")
    @mock.patch.object(server, "ask_openai", return_value="answer")
    def test_text_event(self, ask, reply):
        payload = {
            "header": {"event_id": "evt-2"},
            "event": {"sender": {"sender_id": {"open_id": "ou_1"}}, "message": {
                "message_id": "om_1", "message_type": "text", "chat_type": "p2p",
                "chat_id": "oc_1", "content": json.dumps({"text": "你好"}),
            }},
        }
        server.handle_event(payload)
        ask.assert_called_once_with("oc_1", "你好")
        reply.assert_called_once_with("om_1", "answer")

    @mock.patch.object(server, "ask_openai")
    def test_group_requires_mention(self, ask):
        payload = {"header": {"event_id": "evt-3"}, "event": {"message": {
            "message_id": "om_2", "message_type": "text", "chat_type": "group",
            "chat_id": "oc_2", "content": json.dumps({"text": "hello"}),
        }}}
        server.handle_event(payload)
        ask.assert_not_called()


if __name__ == "__main__":
    unittest.main()
