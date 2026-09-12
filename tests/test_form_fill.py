import json
import unittest
from unittest.mock import AsyncMock, Mock

from utilities.form_fill import verified_fill, verification_script


class VerifiedFillTests(unittest.IsolatedAsyncioTestCase):
    async def test_batch_checks_each_uid_on_same_page(self):
        fill = Mock(ainvoke=AsyncMock(return_value='Successfully filled'))
        evaluate = Mock(ainvoke=AsyncMock(side_effect=[
            [{'type': 'text', 'text': '{"fillVerification":"verified"}'}],
            'Script returned: {"fillVerification":"repaired"}',
        ]))
        args = {'pageId': 2, 'elements': [
            {'uid': 'school', 'value': 'Purdue'}, {'uid': 'other', 'value': ''}]}
        result = json.loads(await verified_fill('fill_form', args,
                            {'fill_form': fill, 'evaluate_script': evaluate}))
        self.assertEqual([x['status'] for x in result['field_verification']],
                         ['verified', 'repaired'])
        self.assertEqual(evaluate.ainvoke.call_args_list[1].args[0]['args'], ['other'])
        self.assertEqual(evaluate.ainvoke.call_args.args[0]['pageId'], 2)

    async def test_failed_or_unreadable_verification_is_not_success(self):
        for output in ('{"fillVerification":"failed"}', 'Script error'):
            fill = Mock(ainvoke=AsyncMock(return_value='Successfully filled'))
            evaluate = Mock(ainvoke=AsyncMock(return_value=output))
            result = json.loads(await verified_fill('fill', {'uid': 'school', 'value': 'Purdue'},
                                {'fill': fill, 'evaluate_script': evaluate}))
            self.assertIn('error', result)
            fill.ainvoke.assert_awaited_once()

    async def test_missing_verifier_does_not_claim_success(self):
        with self.assertRaises(RuntimeError):
            await verified_fill('fill', {'uid': 'school', 'value': ''},
                                {'fill': AsyncMock(return_value='Success')})


class BrowserReplacementTests(unittest.IsolatedAsyncioTestCase):
    async def test_controlled_input_clear_replace_and_refusal(self):
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chrome', headless=True)
            try:
                page = await browser.new_page()
                await page.set_content('<input id="school"><select><option>A</option></select>')
                await page.evaluate('''() => {
                    const el = document.querySelector('input');
                    let state = 'Old universityPurduePurdue';
                    const native = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
                    native.set.call(el, state);
                    Object.defineProperty(el, 'value', {
                      get() { return native.get.call(this); },
                      set(v) { state = v; native.set.call(this, v); }
                    });
                    el.addEventListener('input', () => { state = el.value; });
                    el.addEventListener('blur', () => native.set.call(el, state));
                }''')
                field = page.locator('#school')
                name = 'Purdue "Northwest" \\ campus'
                self.assertEqual(await field.evaluate(verification_script(name)),
                                 {'fillVerification': 'repaired'})
                self.assertEqual(await field.input_value(), name)
                self.assertEqual(await field.evaluate(verification_script(name)),
                                 {'fillVerification': 'verified'})
                self.assertEqual(await field.evaluate(verification_script('')),
                                 {'fillVerification': 'repaired'})
                self.assertEqual(await field.input_value(), '')
                self.assertEqual(await page.locator('select').evaluate(verification_script('A')),
                                 {'fillVerification': 'not_applicable'})
                await field.evaluate("el => el.addEventListener('input', () => el.value = 'locked')")
                self.assertEqual(await field.evaluate(verification_script('Purdue')),
                                 {'fillVerification': 'failed'})
            finally:
                await browser.close()
