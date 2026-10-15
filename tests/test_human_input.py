import io
import os
import unittest
from unittest.mock import patch

from utilities.human_input import TIMEOUT_RESPONSE, timed_human_input, timeout_update


class HumanInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_typing_across_timer_ticks_keeps_response_line_intact(self):
        import pty

        master, slave = pty.openpty()
        now = 0
        output = io.StringIO()
        chunks = iter([b'CD', b'\n'])

        async def tick(_delay):
            nonlocal now
            # No timer output may overwrite text echoed by the terminal.
            self.assertEqual(output.getvalue(), 'Question?\nTime to respond: 1:00\n> ')
            now += 1
            os.write(master, next(chunks))

        try:
            with os.fdopen(slave) as stream:
                os.write(master, b'AB')
                with patch('utilities.human_input.asyncio.sleep', side_effect=tick):
                    result = await timed_human_input(
                        'Question?', stream=stream, output=output, clock=lambda: now)
                self.assertEqual(result, 'ABCD')
        finally:
            os.close(master)

    async def test_reply_within_deadline(self):
        reader, writer = os.pipe()
        with os.fdopen(reader) as stream, os.fdopen(writer, 'w') as sender:
            sender.write('Yes\n')
            sender.flush()
            output = io.StringIO()
            result = await timed_human_input('Question?', stream=stream, output=output)
            self.assertEqual(result, 'Yes')
            self.assertIn('1:00', output.getvalue())

    async def test_timeout_does_not_leave_reader_behind(self):
        reader, writer = os.pipe()
        with os.fdopen(reader) as stream, os.fdopen(writer, 'w') as sender:
            result = await timed_human_input('Question?', .01, stream=stream, output=io.StringIO())
            self.assertEqual(result, TIMEOUT_RESPONSE)
            sender.write('Next reply\n')
            sender.flush()
            self.assertEqual(stream.readline(), 'Next reply\n')

    async def test_deadline_wins_over_late_input(self):
        reader, writer = os.pipe()
        with os.fdopen(reader) as stream, os.fdopen(writer, 'w') as sender:
            sender.write('Late\n')
            sender.flush()
            times = iter([0, 0, 61])
            result = await timed_human_input('?', stream=stream, output=io.StringIO(), clock=lambda: next(times))
            self.assertEqual(result, TIMEOUT_RESPONSE)

    def test_question_fallback_then_skip_if_still_blocked(self):
        state = dict(job_url='job1', human_intervention_origin='submit_job_application',
                     human_intervention_reason='QUESTION', human_intervention_prompt='Why this job?')
        result = timeout_update(state)
        self.assertEqual(result['next_step'], 'submit_job_application')
        self.assertIn('best reasonable inference', result['submit_job_application_messages'][0]['content'])
        self.assertNotIn('user_profile', result)
        result = timeout_update({**state, 'timed_out_questions': result['timed_out_questions']})
        self.assertEqual(result['next_step'], 'update_job_status')

    def test_blockers_skip_from_every_origin(self):
        for origin in ('perform_login', 'resolve_application_url', 'submit_job_application'):
            for reason in ('CAPTCHA', 'SECURITY', 'OTHER'):
                result = timeout_update(dict(human_intervention_origin=origin, human_intervention_reason=reason))
                self.assertEqual(result['next_step'], 'update_job_status')
                self.assertEqual(result['application_status'], 'Not Applied')
                self.assertFalse(result['requires_human'])

    async def test_node_timeout_and_on_time_reply(self):
        import ast
        from pathlib import Path
        from unittest.mock import Mock
        source = Path('application_nodes.py')
        node = next(n for n in ast.parse(source.read_text()).body
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == 'human_in_the_loop')
        node.decorator_list = []
        save = Mock(return_value={})
        interrupt = Mock(return_value=TIMEOUT_RESPONSE)
        namespace = dict(log_node_stage=Mock(), interrupt=interrupt,
                         load_profile=lambda: {}, save_answer=save,
                         question_context=lambda *args: '',
                         HumanMessage=lambda content: {'role': 'user', 'content': content})
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
        state = dict(human_intervention_origin='submit_job_application',
                     human_intervention_reason='QUESTION', human_intervention_prompt='Why?')
        result = await namespace['human_in_the_loop'](state)
        self.assertEqual(interrupt.call_args.args[0]['timeout_seconds'], 60)
        self.assertEqual(result['next_step'], 'submit_job_application')
        save.assert_not_called()
        interrupt.return_value = 'My answer'
        result = await namespace['human_in_the_loop'](state)
        self.assertEqual(result['submit_job_application_messages'][0]['content'], 'My answer')
        save.assert_called_once()
