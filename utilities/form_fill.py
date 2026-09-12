"""Verify browser fills and repair stale state in controlled text inputs."""

import json
import re


def verification_script(value: str) -> str:
    # JSON encoding keeps user text data, never executable JavaScript.
    return """async (el) => {
      const expected = EXPECTED;
      const win = el.ownerDocument.defaultView;
      const textTypes = ['text', 'search', 'email', 'tel', 'url', 'password',
                         'number', 'date', 'datetime-local', 'month', 'week', 'time'];
      const supported = el instanceof win.HTMLTextAreaElement ||
        (el instanceof win.HTMLInputElement && textTypes.includes(el.type));
      if (!supported) return {fillVerification: 'not_applicable'};
      if (el.disabled || el.readOnly) return {fillVerification: 'failed'};
      const settle = () => new Promise(resolve => win.setTimeout(resolve, 100));
      el.blur();
      await settle();
      if (el.value === expected) return {fillVerification: 'verified'};
      // Bypass framework instance setters so input events update application state.
      const proto = el instanceof win.HTMLTextAreaElement
        ? win.HTMLTextAreaElement.prototype : win.HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
      el.focus();
      setter.call(el, '');
      el.dispatchEvent(new win.Event('input', {bubbles: true}));
      el.dispatchEvent(new win.Event('change', {bubbles: true}));
      await settle();
      if (!el.isConnected || el.value !== '') return {fillVerification: 'failed'};
      setter.call(el, expected);
      el.dispatchEvent(new win.Event('input', {bubbles: true}));
      el.dispatchEvent(new win.Event('change', {bubbles: true}));
      el.blur();
      await settle();
      return {fillVerification: el.isConnected && el.value === expected
        ? 'repaired' : 'failed'};
    }""".replace("EXPECTED", json.dumps(value))


async def verified_fill(tool_name, tool_args, tools):
    """Keep native control handling; check every text target before claiming success."""
    result = await tools[tool_name].ainvoke(tool_args)
    evaluator = tools.get('evaluate_script')
    if evaluator is None:
        raise RuntimeError('Fill ran but value verification is unavailable. Inspect before retrying.')
    elements = tool_args['elements'] if tool_name == 'fill_form' else [tool_args]
    statuses = []
    for element in elements:
        args = {'function': verification_script(element['value']), 'args': [element['uid']]}
        if 'pageId' in tool_args:
            args['pageId'] = tool_args['pageId']
        check = await evaluator.ainvoke(args)
        # MCP adapters return text content blocks (or a plain string).
        text = check if isinstance(check, str) else json.dumps(check, default=str)
        normalized = text.replace('\\"', '"')
        match = re.search(r'"fillVerification"\s*:\s*"(verified|repaired|not_applicable|failed)"', normalized)
        status = match.group(1) if match else 'failed'
        statuses.append({'uid': element['uid'], 'status': status})
        if status == 'failed':
            return json.dumps({'error': 'Field replacement could not be verified. Do not repeat '
                               'the same fill; inspect the current control before continuing.',
                               'fields': statuses})
    return json.dumps({'result': result, 'field_verification': statuses}, default=str)
