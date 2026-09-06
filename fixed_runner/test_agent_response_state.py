import unittest
from agent_response_state import response_state


def message(text, finish='stop', error=None):
    return [{'info': {'id':'reply', 'role':'assistant', 'finish':finish, 'error':error},
             'parts':[{'type':'text','text':text}]}]


class ResponseStateTests(unittest.TestCase):
    def test_real_missing_question_regression(self):
        result = response_state(message('当前设备状态：\n虚拟机2已连接。\n跑一轮视频任务前，还差几个参数，请确认：'), 'idle', [])
        self.assertEqual(result['phase'], 'incomplete')
        self.assertEqual(result['reason_code'], 'missing_questions')
        self.assertTrue(result['auto_recoverable'])

    def test_real_question_or_complete_answer_not_recovered(self):
        for text in ['请确认：设备2，视频2条。', '参数如下：\n- 条数：2', '请确认视频数量是多少？', '任务已经完成。']:
            self.assertFalse(response_state(message(text), 'idle', [])['auto_recoverable'])
        self.assertEqual(response_state(message('请确认：'), 'busy', [{'id':'q'}])['phase'], 'waiting_user')

    def test_length_error_and_tool_states_distinct(self):
        self.assertEqual(response_state(message('一半', 'length'), 'idle', [])['reason_code'], 'response_truncated')
        self.assertEqual(response_state(message('', None, {'secret':'never return'}), 'idle', [])['phase'], 'failed')
        self.assertNotIn('secret', str(response_state(message('', None, {'secret':'never return'}), 'idle', [])))
        tools=[{'info':{'role':'assistant'},'parts':[{'type':'tool','state':{'status':'running'}}]}]
        self.assertEqual(response_state(tools, 'busy', [])['phase'], 'tool_running')
