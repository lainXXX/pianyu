"""Settings acceptance: all credentials and uploads are isolated synthetic fixtures."""
import json
from pathlib import Path

import pytest
from test_web_ui import server, browser

pytestmark = pytest.mark.integration


@pytest.fixture
def settings_page(browser, server):
    ctx = browser.new_context(accept_downloads=True)
    page = ctx.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(server[0] + '/settings')
    page.wait_for_selector('body[data-ready="1"]')
    yield page
    ctx.close()
    assert errors == []


def tab(page, name):
    page.click('#tab-' + name)


def new_editor(page, form):
    if form in ('out-form', 'tpl-form'):
        page.locator(f'button[data-new-editor="{form}"]').click()
    else:
        page.locator('details', has=page.locator('#' + form)).locator(':scope > summary').click()


def row(page, table, name):
    return page.locator('#' + table + ' tbody tr', has_text=name)


def add_engine(page, name='独立转写'):
    tab(page, 'asr')
    new_editor(page, 'engine-form')
    page.select_option('#ef-preset', 'custom')
    page.fill('#ef-label', name)
    page.fill('#ef-url', 'https://api.example.com/v1')
    page.fill('#ef-model', 'whisper-1')
    page.fill('#ef-key', 'fake-settings-secret')
    page.click('#ef-save')
    page.wait_for_selector(f'#engines tbody tr:has-text("{name}")')
    return row(page, 'engines', name)


def test_service_mask_reveal_keep_test_default_delete(settings_page, server):
    p = settings_page
    r = add_engine(p)
    sid = r.locator('[data-action=edit]').get_attribute('data-id')
    assert r.locator('[data-action=test]').count() == 0
    assert '内置' not in p.inner_text('#engines')
    secret_calls = []
    p.on('request', lambda request: secret_calls.append(request.url) if request.url.endswith('/secrets') else None)
    r.locator('[data-action=edit]').click()
    assert p.input_value('#ef-key') == '********'
    assert secret_calls == []
    p.click('#ef-key + button')
    p.wait_for_function("document.getElementById('ef-key').value === 'fake-settings-secret'")
    p.click('#ef-key + button')
    assert p.input_value('#ef-key') == '********'
    p.click('#ef-test')
    p.wait_for_selector('#engine-msg:has-text("连接成功")')
    assert p.locator('#engine-form #engine-msg').count() == 1
    p.fill('#ef-label', '独立转写修改')
    assert p.is_disabled('#ef-test') and p.inner_text('#ef-test') == '保存后测试'
    p.click('#ef-save')
    p.wait_for_selector('#engines tr:has-text("独立转写修改")')
    assert p.request.get(server[0] + f'/api/engines/{sid}/secrets').json()['key'] == 'fake-settings-secret'
    r = row(p, 'engines', '独立转写修改')
    r.locator('[data-action=set-default]').click()
    p.wait_for_function(f"document.querySelector('[data-action=\"set-default\"][data-id=\"{sid}\"]').getAttribute('aria-pressed') === 'true'")
    p.reload(); p.wait_for_selector('body[data-ready="1"]')
    assert row(p, 'engines', '独立转写修改').locator('[data-action=set-default]').get_attribute('aria-pressed') == 'true'
    p.once('dialog', lambda d: d.accept())
    row(p, 'engines', '独立转写修改').locator('[data-action=delete]').click()
    p.wait_for_function("!document.querySelector('#engines').innerText.includes('独立转写修改')")
    p.reload(); p.wait_for_selector('body[data-ready="1"]')
    assert row(p, 'engines', '独立转写修改').count() == 0


def test_tencent_partial_secret_update_failure(settings_page, server):
    p = settings_page
    new_editor(p, 'engine-form')
    p.select_option('#ef-preset', 'tencent')
    p.fill('#ef-label', '腾讯测试')
    p.fill('#ef-secret_id', 'AKIDfake')
    p.click('#ef-save')
    p.wait_for_selector('#engine-msg:has-text("SecretKey")')
    p.fill('#ef-secret_key', 'fake-key-one')
    p.click('#ef-save')
    p.wait_for_selector('#engines tr:has-text("腾讯测试")')
    r = row(p, 'engines', '腾讯测试')
    sid = r.locator('[data-action=edit]').get_attribute('data-id')
    r.locator('[data-action=edit]').click()
    assert p.input_value('#ef-secret_id') == p.input_value('#ef-secret_key') == '********'
    p.route(f'**/api/engines/{sid}/test', lambda route: route.fulfill(json={'ok':False, 'message':'连接失败：无效凭据'}))
    p.click('#ef-test')
    p.wait_for_selector('#engine-msg:has-text("失败")')
    p.fill('#ef-secret_key', 'fake-key-two')
    p.click('#ef-save')
    p.wait_for_function("!document.getElementById('engine-form').closest('details').open")
    assert p.request.get(server[0] + f'/api/engines/{sid}/secrets').json() == {'secret_id':'AKIDfake', 'secret_key':'fake-key-two'}


def test_service_private_url_validation(settings_page):
    p = settings_page
    new_editor(p, 'engine-form')
    p.select_option('#ef-preset', 'custom')
    p.fill('#ef-label', '内网服务')
    p.fill('#ef-url', 'http://192.168.1.10/v1')
    p.fill('#ef-model', 'model')
    p.fill('#ef-key', 'fake-only')
    p.click('#ef-save')
    p.wait_for_selector('#engine-msg:has-text("内网")')
    assert row(p, 'engines', '内网服务').count() == 0
    p.locator('#engine-form').get_by_label('允许内网地址').check()
    p.click('#ef-save')
    r = row(p, 'engines', '内网服务')
    r.wait_for()
    r.locator('[data-action=edit]').click()
    assert p.is_checked('#ef-local')


@pytest.mark.parametrize('width', [1440, 390])
def test_llm_private_domain_opt_in_save_refresh_and_test(settings_page, server, width):
    p = settings_page
    p.set_viewport_size({'width': width, 'height': 900})
    tab(p, 'llm'); new_editor(p, 'llm-form')
    p.select_option('#lf-preset', 'custom')
    name = f'GPT 内网 {width}'
    p.fill('#lf-label', name)
    p.fill('#lf-url', 'https://ai.internal.example.com/v1')
    p.fill('#lf-model', 'gpt-6.1-sol')
    p.fill('#lf-context', '1000000')
    p.fill('#lf-key', 'fake-private-ui-key')
    p.click('#lf-save')
    p.wait_for_selector('#llm-msg:has-text("允许内网地址")')
    assert row(p, 'llms', name).count() == 0
    p.locator('#llm-form').get_by_label('允许内网地址').check()
    p.click('#lf-save')
    row(p, 'llms', name).wait_for()
    p.reload(); p.wait_for_selector('body[data-ready="1"]')
    row(p, 'llms', name).locator('[data-action=edit]').click()
    assert p.is_checked('#lf-local')
    assert p.input_value('#lf-key') == '********'
    assert p.input_value('#lf-url') == 'https://ai.internal.example.com/v1'
    assert p.input_value('#lf-context') == '1000000'
    p.click('#lf-test'); p.wait_for_selector('#llm-msg:has-text("连接成功")')
    assert p.evaluate('document.documentElement.scrollWidth <= innerWidth')
    out = Path('acceptance/private-model'); out.mkdir(parents=True, exist_ok=True)
    p.evaluate("document.activeElement.blur(); window.scrollTo({top: 0, behavior: 'instant'})")
    p.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    assert p.locator('header').bounding_box()['y'] == 0
    p.screenshot(path=str(out / f'settings-{width}.png'), full_page=True)


def test_llm_create_reveal_dirty_test_default(settings_page, server):
    p = settings_page
    tab(p, 'llm'); new_editor(p, 'llm-form')
    p.select_option('#lf-preset', 'custom')
    p.fill('#lf-label', '设置测试模型')
    p.fill('#lf-url', 'https://api.example.com/v1')
    p.fill('#lf-model', 'chat-test')
    p.fill('#lf-key', 'fake-llm-settings')
    p.click('#lf-save'); p.wait_for_selector('#llms tr:has-text("设置测试模型")')
    r = row(p, 'llms', '设置测试模型')
    sid = r.locator('[data-action=edit]').get_attribute('data-id')
    r.locator('[data-action=edit]').click()
    assert p.input_value('#lf-key') == '********'
    p.click('#lf-key + button')
    p.wait_for_function("document.getElementById('lf-key').value === 'fake-llm-settings'")
    p.click('#lf-key + button')
    p.click('#lf-test'); p.wait_for_selector('#llm-msg:has-text("连接成功")')
    p.fill('#lf-label', '设置模型修改')
    assert not p.is_disabled('#lf-test')
    p.click('#lf-test'); p.wait_for_selector('#llm-msg:has-text("连接成功")')
    p.click('#lf-save'); p.wait_for_selector('#llms tr:has-text("设置模型修改")')
    assert p.request.get(server[0] + f'/api/llm/services/{sid}/secrets').json()['key'] == 'fake-llm-settings'
    row(p, 'llms', '设置模型修改').locator('[data-action=set-default]').click()
    p.wait_for_function(f"document.querySelector('#llms [data-action=\"set-default\"][data-id=\"{sid}\"]').getAttribute('aria-pressed') === 'true'")


@pytest.mark.parametrize('width', [1440, 390])
def test_new_llm_test_without_saving(settings_page, server, width):
    p = settings_page
    p.set_viewport_size({'width': width, 'height': 900})
    tab(p, 'llm'); new_editor(p, 'llm-form')
    p.select_option('#lf-preset', 'custom')
    name = f'未保存测试 {width}'
    p.fill('#lf-label', name)
    p.fill('#lf-url', 'https://ai.internal.example.com/v1')
    p.check('#lf-local')
    p.fill('#lf-model', 'gpt-6.1-sol')
    p.fill('#lf-key', 'fake-draft-only-key')
    before = p.request.get(server[0] + '/api/llm/services').json()
    with p.expect_request(lambda r: r.method == 'POST' and '/api/llm/services/test' in r.url) as request:
        p.click('#lf-test')
    assert request.value.post_data_json['allow_local'] is True
    p.wait_for_selector('#llm-msg:has-text("连接成功")')
    assert p.request.get(server[0] + '/api/llm/services').json() == before
    assert row(p, 'llms', name).count() == 0
    p.fill('#lf-model', 'changed-model')
    assert p.locator('#llm-msg').inner_text() == ''
    assert not p.is_disabled('#lf-test')
    p.route('**/api/llm/services/test', lambda route: route.fulfill(json={
        'ok': False, 'code': 'LLM_ACCESS_DENIED', 'message': '凭证无效',
    }))
    p.click('#lf-test'); p.wait_for_selector('#llm-msg:has-text("凭证无效")')
    assert not p.is_disabled('#lf-test')
    assert p.evaluate('document.documentElement.scrollWidth <= innerWidth')
    out = Path('acceptance/draft-model'); out.mkdir(parents=True, exist_ok=True)
    p.evaluate("document.activeElement.blur(); window.scrollTo({top: 0, behavior: 'instant'})")
    p.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    p.screenshot(path=str(out / f'new-model-{width}.png'), full_page=True)
    p.click('#lf-save')
    row(p, 'llms', name).wait_for()
    p.reload(); p.wait_for_selector('body[data-ready="1"]')
    row(p, 'llms', name).locator('[data-action=edit]').click()
    assert p.input_value('#lf-model') == 'changed-model'


def test_changed_model_discards_pending_test_feedback(settings_page):
    p = settings_page
    tab(p, 'llm'); new_editor(p, 'llm-form')
    p.select_option('#lf-preset', 'custom')
    p.fill('#lf-label', '测试反馈')
    p.fill('#lf-url', 'https://api.example.com/v1')
    p.fill('#lf-model', 'old-model')
    p.fill('#lf-key', 'fake-late-test')
    pending = []
    p.route('**/api/llm/services/test', lambda route: pending.append(route))
    p.click('#lf-test')
    p.wait_for_function("document.getElementById('lf-test').textContent === '测试中…'")
    assert p.is_disabled('#lf-test')
    p.fill('#lf-model', 'new-model')
    assert not p.is_disabled('#lf-test')
    assert len(pending) == 1
    with p.expect_response('**/api/llm/services/test'):
        pending.pop().fulfill(json={'ok': True})
    p.evaluate('() => new Promise(resolve => setTimeout(resolve, 100))')
    assert p.locator('#llm-msg').inner_text() == ''
    assert p.inner_text('#lf-test') == '测试连接'


def test_independent_style_create_validation_bind_edit_hide_reset(settings_page, server):
    p = settings_page
    tab(p, 'outputs')
    assert p.locator('#of-copy,[data-action=copy]').count() == 0
    new_editor(p, 'out-form')
    assert p.input_value('#of-name') == p.input_value('#of-prompt') == ''
    p.select_option('#of-kind', 'points')
    assert p.locator('#of-layout-boxes input[value=book]').count() == 0
    p.fill('#of-name', '独立风格')
    p.fill('#of-prompt', '请总结')
    p.check('#of-layout-boxes input[value=A]'); p.check('#of-layout-boxes input[value=B]')
    p.click('#of-save'); p.wait_for_selector('#out-msg:has-text("{text}")')
    p.fill('#of-prompt', '请按 JSON 总结：{text}')
    p.click('#of-save'); p.wait_for_selector('#out-list li:has-text("独立风格")')
    outputs = p.request.get(server[0] + '/api/outputs').json()['outputs']
    custom = next(o for o in outputs if o['name'] == '独立风格')
    assert custom['layouts'] == ['A', 'B'] and custom['kind'] == 'points'
    p.locator('#out-list li', has_text='独立风格').locator('[data-action=edit]').click()
    assert p.is_disabled('#of-kind')
    p.fill('#of-name', '独立风格修改'); p.click('#of-save')
    p.wait_for_selector('#out-list li:has-text("独立风格修改")')
    r = p.locator('#out-list li', has_text='精校原文')
    r.locator('[data-action=edit]').click()
    p.fill('#of-prompt', '保留事实。\n' + p.input_value('#of-prompt')); p.click('#of-save')
    p.wait_for_selector('#out-list li:has-text("精校原文") [data-action=reset]')
    r.locator('[data-action=reset]').click()
    p.wait_for_function("!document.querySelector('#out-list [data-id=refine][data-action=reset]')")
    r.locator('[data-action=hide]').click(); p.wait_for_function("!document.querySelector('#out-list [data-id=refine]')")
    p.check('#out-show-hidden'); p.locator('#out-list [data-id=refine][data-action=show]').click()
    p.wait_for_selector('#out-list [data-id=refine][data-action=hide]')
    p.once('dialog', lambda d:d.accept())
    p.locator('#out-list li', has_text='独立风格修改').locator('[data-action=delete]').click()
    p.wait_for_function("!document.querySelector('#out-list').innerText.includes('独立风格修改')")


def test_layout_blank_create_multi_bind_preview_and_guard(settings_page, server):
    p = settings_page
    tab(p, 'layouts')
    assert p.locator('#tf-copy').count() == 0
    assert not p.locator('#tpl-form').is_visible()
    p.locator('#lay-list-article [data-action=preview][data-id=night]').click()
    p.wait_for_function("document.getElementById('tpl-preview-img').naturalWidth === 1080")
    assert p.locator('#layout-preview').is_visible() and not p.locator('#tpl-form').is_visible()
    p.keyboard.press('Escape')
    new_editor(p, 'tpl-form')
    assert p.input_value('#tf-name') == '' and 'class="content"' in p.input_value('#tf-html')
    p.fill('#tf-name', '全新文章版式')
    p.fill('#tf-width', '640'); p.fill('#tf-height-px', '900')
    p.check('#tf-output-boxes input[value=structure]'); p.check('#tf-output-boxes input[value=refine]')
    p.click('#tf-preview')
    p.wait_for_function("document.getElementById('tpl-preview-img').naturalWidth === 640")
    assert 0 < p.evaluate("document.getElementById('tpl-preview-img').naturalHeight") <= 900
    p.click('#preview-close'); p.click('#tf-save')
    p.wait_for_selector('#lay-list-article li:has-text("全新文章版式")')
    layouts = p.request.get(server[0] + '/api/layouts').json()['layouts']
    layout = next(t for t in layouts if t['name'] == '全新文章版式')
    outputs = p.request.get(server[0] + '/api/outputs?hidden=true').json()['outputs']
    assert layout['height_mode'] == 'fixed:900'
    assert all(layout['id'] in next(o for o in outputs if o['id'] == oid)['layouts'] for oid in ['structure','refine'])
    tab(p, 'outputs')
    p.locator('#out-list [data-action=edit][data-id=refine]').click()
    assert p.locator(f'#of-layout-boxes input[value="{layout["id"]}"]').is_checked()
    # Make this layout the only one for a new style, then reject unbinding its last layout.
    p.click('#of-cancel'); new_editor(p, 'out-form')
    p.fill('#of-name', '单版式风格'); p.fill('#of-prompt', '{text}')
    p.check(f'#of-layout-boxes input[value="{layout["id"]}"]'); p.click('#of-save')
    p.wait_for_selector('#out-list li:has-text("单版式风格")')
    styles = p.request.get(server[0] + '/api/outputs').json()['outputs']; only = next(o for o in styles if o['name'] == '单版式风格')
    tab(p, 'layouts'); p.locator(f'#lay-list-article [data-action=edit][data-id="{layout["id"]}"]').click()
    p.locator(f'#tf-output-boxes input[value="{only["id"]}"]').click()
    assert p.locator(f'#tf-output-boxes input[value="{only["id"]}"]').is_checked()
    assert '替代版式' in p.inner_text('#tpl-msg')
    p.fill('#tf-html', '{% for p in points %}')
    p.click('#tf-preview'); p.wait_for_selector('#preview-msg:has-text("模板")')


def test_layout_binding_failure_preserves_form_and_server(settings_page, server):
    p = settings_page; tab(p, 'layouts')
    p.locator('#lay-list-article [data-action=edit][data-id=night]').click()
    original = p.request.get(server[0] + '/api/outputs?hidden=true').json()
    p.fill('#tf-name', '未保存的夜色')
    p.route('**/api/layouts/night', lambda route: route.fulfill(status=422, json={'detail':'绑定保存失败'}))
    p.click('#tf-save'); p.wait_for_selector('#tpl-msg:has-text("绑定保存失败")')
    assert p.input_value('#tf-name') == '未保存的夜色'
    assert p.request.get(server[0] + '/api/outputs?hidden=true').json() == original
    assert '夜色' not in p.inner_text('#lay-list-article') or '未保存的夜色' not in p.inner_text('#lay-list-article')


@pytest.mark.parametrize('form,prefix,tab_name,endpoint,original_id', [
    ('out-form', 'of', 'outputs', 'outputs', 'refine'),
    ('tpl-form', 'tf', 'layouts', 'layouts', 'night'),
])
@pytest.mark.parametrize('viewport', [{'width': 1440, 'height': 1000}, {'width': 390, 'height': 844}], ids=['desktop', 'mobile'])
def test_new_is_separate_from_edit_and_persists_independently(settings_page, server, form, prefix, tab_name, endpoint, original_id, viewport):
    p = settings_page
    p.set_viewport_size(viewport)
    tab(p, tab_name)
    before = p.request.get(server[0] + f'/api/{endpoint}').json()[endpoint]
    p.locator(f'#panel-{tab_name} [data-action=edit][data-id={original_id}]').click()
    dialog = p.locator('dialog', has=p.locator('#' + form))
    assert dialog.is_visible()
    assert p.input_value(f'#{prefix}-id') == original_id
    assert dialog.get_attribute('aria-labelledby')
    p.fill(f'#{prefix}-name', '不应保存的编辑')
    p.keyboard.press('Escape')
    assert not dialog.is_visible()
    assert p.locator(f'#panel-{tab_name} [data-action=edit][data-id={original_id}]').evaluate('e => e === document.activeElement')
    new_editor(p, form)
    assert dialog.is_visible()
    assert p.input_value(f'#{prefix}-id') == p.input_value(f'#{prefix}-name') == ''
    assert not p.is_disabled('#of-kind' if prefix == 'of' else '#tf-group')
    if prefix == 'of':
        assert p.locator('#of-kind option').all_text_contents() == ['原文类 · 结构化', '原文类 · 精校', '要点类', '自由类']
        assert p.input_value('#of-prompt') == ''
        assert p.locator('#of-layout-boxes input:checked').count() == 0
        p.fill('#of-prompt', '只依据原文：{text}')
        p.check('#of-layout-boxes input[value=book]')
    else:
        assert p.locator('#tf-output-boxes input:checked').count() == 0
        assert 'class="content"' in p.input_value('#tf-html')
    name = f'独立窗口-{prefix}-{viewport["width"]}'
    p.fill(f'#{prefix}-name', name)
    assert dialog.evaluate('e => e.scrollWidth <= e.clientWidth')
    evidence = Path('acceptance/settings-independent'); evidence.mkdir(parents=True, exist_ok=True)
    p.screenshot(path=str(evidence / f'{prefix}-{viewport["width"]}-new.png'))
    with p.expect_request(lambda request: request.method == 'POST' and request.url.endswith('/api/' + endpoint)) as saving:
        p.click(f'#{prefix}-save')
    assert 'copy_of' not in saving.value.post_data_json
    p.wait_for_function('(id) => !document.getElementById(id).closest("dialog").open', arg=form)
    after = p.request.get(server[0] + f'/api/{endpoint}').json()[endpoint]
    created = next(item for item in after if item['name'] == name)
    assert created['id'] not in {item['id'] for item in before}
    assert [item for item in after if item['id'] != created['id']] == before
    p.reload(); p.wait_for_selector('body[data-ready="1"]'); tab(p, tab_name)
    p.locator(f'#panel-{tab_name} [data-action=edit][data-id="{created["id"]}"]').click()
    p.fill(f'#{prefix}-name', name + '-修改')
    p.screenshot(path=str(evidence / f'{prefix}-{viewport["width"]}-edit.png'))
    p.click(f'#{prefix}-save')
    p.wait_for_function('(id) => !document.getElementById(id).closest("dialog").open', arg=form)
    edited = p.request.get(server[0] + f'/api/{endpoint}').json()[endpoint]
    assert next(item for item in edited if item['id'] == created['id'])['name'] == name + '-修改'
    assert [item for item in edited if item['id'] != created['id']] == before


def test_cookie_platform_login_and_proxy(settings_page, server):
    p = settings_page; tab(p, 'cookie')
    assert p.locator('.cookie-platform').count() == 3
    assert p.locator('#cookie-platforms input[type=file]').count() == 0
    p.fill('#proxy','http://127.0.0.1:7897'); p.click('#save-proxy')
    p.wait_for_selector('#settings-msg:has-text("已保存")')
    p.reload(); p.wait_for_selector('body[data-ready="1"]')
    assert p.input_value('#proxy') == 'http://127.0.0.1:7897'


def test_global_rules_and_frame_persist(settings_page, server):
    p = settings_page; tab(p, 'system')
    assert p.input_value('#system-prompt')
    p.fill('#system-prompt','忠实于来源，规范 3D 与英文大小写；遵循输出格式。')
    p.locator('#system-form button[type=submit]').click(); p.wait_for_selector('#system-msg:has-text("已保存")')
    p.reload(); p.wait_for_selector('body[data-ready="1"]')
    assert p.input_value('#system-prompt').startswith('忠实于来源')
    tab(p, 'frame'); p.fill('#fr-footer','来源：{title}')
    p.click('#frame-form [data-action=save][data-id=footer]')
    p.wait_for_selector('#frame-msg:has-text("已保存")')
    p.click('#frame-form [data-action=reset][data-id=footer]')
    p.wait_for_selector('#frame-msg:has-text("已恢复默认")')
    assert '{author}' in p.input_value('#fr-footer')


def test_navigation_help_keyboard_and_mobile_visual(settings_page, server):
    p = settings_page
    assert p.get_by_role('link', name='提取', exact=True).count() == 0
    for name in ['asr','llm','outputs','layouts','system','frame','cookie']:
        tab(p, name)
        assert p.locator('#panel-' + name).is_visible()
    tab(p, 'outputs'); p.focus('#tab-outputs'); p.keyboard.press('ArrowRight')
    assert p.locator('#tab-layouts').get_attribute('aria-selected') == 'true'
    p.focus('button[aria-label="版式帮助"]')
    assert p.locator('button[aria-label="版式帮助"]').evaluate("e => getComputedStyle(e,'::after').display") == 'block'
    out = Path('acceptance/settings'); out.mkdir(parents=True, exist_ok=True)
    p.focus('#tab-layouts'); p.mouse.move(0,0); p.evaluate('window.scrollTo(0,0)')
    p.screenshot(path=str(out/'desktop-layouts.png'), full_page=True)
    for name in ['asr','llm','outputs','system','cookie']:
        tab(p,name); p.evaluate('window.scrollTo(0,0)'); p.screenshot(path=str(out/f'desktop-{name}.png'))
    tab(p,'asr'); p.locator('#engines [data-action=edit][data-id=dashscope]').click()
    p.screenshot(path=str(out/'desktop-service-editor.png'))
    p.click('#ef-cancel')
    p.set_viewport_size({'width':390,'height':844})
    for name in ['asr','llm','outputs','layouts','system','cookie']:
        tab(p, name)
        assert p.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), name
    tab(p,'layouts'); new_editor(p, 'tpl-form')
    assert p.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    p.evaluate('window.scrollTo(0,0)')
    p.screenshot(path=str(out/'mobile-layout-editor.png'), full_page=True)
    p.keyboard.press('Escape')
    p.get_by_role('link', name='返回首页', exact=True).click()
    p.wait_for_url(server[0] + '/')
