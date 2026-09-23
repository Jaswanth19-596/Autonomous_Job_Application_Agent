import ast
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, AsyncMock

from utilities.profile_memory import (
    save_answer, load_profile, recalled_answer, applicable_profile, question_context,
)
from utilities.routing import submit_application_decision
from models.schemas import ApplicationNotAppliedSubModel


class ProfileMemoryTests(unittest.TestCase):
    def test_persistence_corrections_and_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profile.json'
            path.write_text(json.dumps({'school': 'Original', 'application_answers': {}}))
            save_answer('Open to relocation?', 'Yes', 'global', '', path)
            profile = save_answer('Open to relocation?', 'No', 'global', '', path)
            self.assertEqual(len(profile['application_answers']), 1)
            self.assertEqual(profile['school'], 'Original')
            self.assertEqual(recalled_answer(load_profile(path), 'Open to relocation?', 'global', ''), 'No')
            profile = save_answer('Interviewed here?', 'Yes, in 2024', 'employer', 'company a', path)
            self.assertEqual(len(applicable_profile(profile, {'job_details': {'companyName': 'Company B'}})['application_answers']), 1)
            self.assertEqual(len(applicable_profile(profile, {'job_details': {'companyName': 'Company A'}})['application_answers']), 2)


class ApplicationHandoffTests(unittest.IsolatedAsyncioTestCase):
    def load_node(self, name, **namespace):
        source = Path(__file__).resolve().parents[1] / 'application_nodes.py'
        node = next(n for n in ast.parse(source.read_text()).body
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
        node.decorator_list = []
        async def progress(_node, _message, operation, **kwargs):
            return await operation
        base = dict(log_node_stage=Mock(), load_profile=lambda: {'application_answers': {}},
                    json=json, applicable_profile=applicable_profile, recalled_answer=recalled_answer,
                    question_context=question_context, load_prompt=Mock(return_value='system'),
                    await_with_progress=progress, logger=Mock(), visible_response_text=str,
                    application_answer_source=lambda state, profile=None: {})
        base.update(namespace)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), base)
        return base[name]

    async def outcome(self, reason, attempts=0, profile=None):
        decision = ApplicationNotAppliedSubModel(requires_human=True,
            human_intervention_reason=reason, human_intervention_prompt='Open to relocation?',
            question_scope='global')
        response = Mock(tool_calls=[], content='Please fill the form')
        node = self.load_node('submit_job_application',
            load_profile=lambda: profile or {'application_answers': {}},
            get_custom_model_with_tools=AsyncMock(return_value=Mock(ainvoke=AsyncMock(return_value=response))),
            get_custom_structured_model=Mock(return_value=Mock(ainvoke=AsyncMock(return_value=Mock(decision=decision)))))
        return await node({'submit_job_application_messages': [], 'application_recovery_attempts': attempts})

    async def test_manual_handoff_becomes_bounded_recovery(self):
        result = await self.outcome('OTHER')
        self.assertFalse(result['requires_human'])
        self.assertEqual(submit_application_decision(result), 'submit_job_application')
        result = await self.outcome('OTHER', 2)
        self.assertFalse(result['requires_human'])
        self.assertEqual(submit_application_decision(result), 'update_job_status')

    async def test_real_questions_and_challenges_can_interrupt(self):
        for reason in ('QUESTION', 'CAPTCHA', 'SECURITY'):
            result = await self.outcome(reason)
            self.assertEqual(submit_application_decision(result), 'human_in_the_loop')

    async def test_saved_question_is_answered_without_interrupting(self):
        result = await self.outcome('QUESTION', profile={'application_answers': {
            'legacy': {'question': 'Open to relocation?', 'answer': 'Yes'}
        }})
        self.assertFalse(result['requires_human'])
        self.assertEqual(result['job_application_phase'], 'CONTINUE')
        self.assertIn("User's saved answer: Yes", result['submit_job_application_messages'][-1]['content'])

    async def test_answer_saved_before_resuming_but_challenge_reply_not_saved(self):
        for reason in ('QUESTION', 'CAPTCHA', 'SECURITY'):
            save = Mock(return_value={'application_answers': {'saved': {}}})
            node = self.load_node('human_in_the_loop', interrupt=Mock(return_value='Yes'), save_answer=save)
            result = await node({'human_intervention_origin': 'submit_job_application',
                'human_intervention_reason': reason, 'human_intervention_prompt': 'Open to relocation?',
                'question_scope': 'global'})
            self.assertEqual(result['next_step'], 'submit_job_application')
            self.assertFalse(result['requires_human'])
            self.assertEqual(save.call_count, 1 if reason == 'QUESTION' else 0)
