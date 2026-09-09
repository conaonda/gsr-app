"""Chromium regression of the v0.2 editor using synthetic video only."""
from __future__ import annotations

import os
import re
from pathlib import Path
import tempfile

import httpx
from playwright.sync_api import expect, sync_playwright

from make_demo_video import make_video

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get('GSR_TEST_URL', 'http://127.0.0.1:8765')
ARTIFACTS = Path(os.environ.get('GSR_TEST_ARTIFACT_DIR', str(ROOT / 'data' / 'editor-qa')))


def ready(page):
    expect(page.locator('#analysisStage')).to_be_visible(timeout=30000)
    expect(page.locator('#sourceCanvasMessage')).to_be_hidden(timeout=30000)
    expect(page.locator('#sourceCanvas')).to_have_attribute('data-viewport-scale', re.compile(r'.+'))


def source_position(page, xy):
    canvas = page.locator('#sourceCanvas')
    canvas.scroll_into_view_if_needed()
    data = canvas.evaluate('c=>({scale:Number(c.dataset.viewportScale),x:Number(c.dataset.viewportOffsetX),y:Number(c.dataset.viewportOffsetY)})')
    assert data['scale'] > 0, data
    box = canvas.bounding_box()
    return box['x'] + data['x'] + xy[0] * data['scale'], box['y'] + data['y'] + xy[1] * data['scale']


def add_pair(page, image, pitch):
    page.mouse.click(*source_position(page, image))
    canvas = page.locator('#pitchCanvas')
    canvas.scroll_into_view_if_needed()
    dims = canvas.evaluate('c=>({w:c.width,h:c.height})')
    w, h = dims['w'], dims['h']
    px, py = max(36, w * .075), max(32, h * .09)
    box = canvas.bounding_box()
    page.mouse.click(box['x'] + (px + pitch[0] * (w-2*px))/w*box['width'],
                     box['y'] + (py + pitch[1] * (h-2*py))/h*box['height'])


def field_value(page, row, column):
    return float(page.locator('#pointTableBody tr').nth(row).locator('input').nth(column).input_value())


def change_number(locator, value):
    locator.fill(str(value))
    locator.press('Tab')


def registration_count(client, video_id):
    response = client.get(f'/api/videos/{video_id}/registrations')
    response.raise_for_status()
    return len(response.json())


def wait_held(page, held):
    for _ in range(100):
        if held:
            return
        page.wait_for_timeout(50)
    raise AssertionError('No preview response reached the real endpoint')


def exercise(page, video, client):
    errors = []
    frame_requests = []
    page.on('request', lambda r: frame_requests.append(r.url) if '/frame/' in r.url else None)
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(BASE)
    expect(page.locator('#apiStateText')).to_contain_text('연결', timeout=10000)
    page.locator('#videoPath').fill(str(video))
    with page.expect_response(lambda r: r.url.endswith('/api/videos') and r.request.method == 'POST') as imported:
        page.locator('#importButton').click()
    record = imported.value.json()
    video_id = record['id']
    ready(page)
    page.locator('#toolAddFit').click()
    expect(page.locator('#estimateButton')).to_be_disabled()
    for image, landmark in [([100,80],'top-left'), ([860,80],'top-right'),
                            ([860,460],'bottom-right'), ([100,460],'bottom-left')]:
        page.mouse.click(*source_position(page, image))
        page.locator('#pitchLandmark').select_option(landmark)
        page.locator('#applyLandmark').click()
    expect(page.locator('#pointCount')).to_have_text('4')
    before = registration_count(client, video_id)
    page.locator('#estimateButton').click()
    expect(page.locator('#resultBody')).to_contain_text('검토 필요', timeout=30000)
    assert registration_count(client, video_id) == before

    # Validation has its own input tool and prediction-free view.
    page.locator('#toolAddValidation').click()
    add_pair(page, [480,240], [.5,160/380])
    add_pair(page, [480,460], [.5,1])
    expect(page.locator('#pointCount')).to_have_text('6')
    page.locator('#toolSelect').click()
    page.locator('#estimateButton').click()
    expect(page.locator('#resultBody')).to_contain_text('사용 가능', timeout=30000)
    assert registration_count(client, video_id) == before
    page.locator('#saveButton').click()
    expect(page.locator('#resultSource')).not_to_contain_text('PREVIEW', timeout=30000)
    expect(page.locator('#historyCount')).to_have_text(str(before+1), timeout=30000)
    saved = client.get(f'/api/videos/{video_id}/registrations').json()[-1]
    for actual, expected in zip(saved['points'][:4], [(100,80),(860,80),(860,460),(100,460)]):
        assert abs(actual['image'][0]-expected[0]) < 1.5
        assert abs(actual['image'][1]-expected[1]) < 1.5

    # A zoomed drag edits native coordinates once, and one undo reverses it.
    page.locator('#oneToOneView').click()
    original = (field_value(page, 4, 0), field_value(page, 4, 1))
    page.mouse.move(*source_position(page, original))
    page.mouse.wheel(0, -300)
    page.locator('#sourceCanvas').evaluate('c=>c.focus()')
    start = source_position(page, original)
    scale = float(page.locator('#sourceCanvas').get_attribute('data-viewport-scale'))
    page.mouse.move(*start)
    page.mouse.down()
    page.mouse.move(start[0]+10*scale, start[1]+6*scale, steps=6)
    page.mouse.up()
    moved = (field_value(page, 4, 0), field_value(page, 4, 1))
    assert abs(moved[0]-original[0]-10) < 1.5, (original, moved)
    assert abs(moved[1]-original[1]-6) < 1.5, (original, moved)
    page.locator('#undoButton').click()
    assert abs(field_value(page,4,0)-original[0]) < .02
    page.locator('#redoButton').click()
    assert abs(field_value(page,4,0)-moved[0]) < .02
    page.locator('#undoButton').click()
    page.locator('#fitView').click()

    # Current-session drafts and history survive a frame round trip.
    change_number(page.locator('#fieldLength'), 60)
    page.locator('#nextFrame').click()
    ready(page)
    expect(page.locator('#pointCount')).to_have_text('0')
    page.locator('#previousFrame').click()
    ready(page)
    expect(page.locator('#pointCount')).to_have_text('6')
    expect(page.locator('#fieldLength')).to_have_value('60')
    assert sum(url.endswith('/frame/0') for url in frame_requests) == 1, frame_requests

    # A pending old draft response must not restore the old quality.
    held = []
    def hold_preview(route):
        response = route.fetch()
        held.append((route, response))
    page.route('**/registrations/preview', hold_preview)
    page.locator('#estimateButton').click()
    wait_held(page, held)
    first_x = page.locator('#pointTableBody tr').nth(0).locator('input').nth(0)
    change_number(first_x, 180)
    for route, response in held:
        try:
            route.fulfill(response=response)
        except Exception as exc:
            # AbortController may have cancelled the obsolete browser request.
            if 'closed' not in str(exc).lower() and 'invalid' not in str(exc).lower():
                raise
    page.unroute('**/registrations/preview', hold_preview)
    expect(page.locator('#resultBody')).not_to_contain_text('사용 가능')
    assert registration_count(client, video_id) == before+1
    page.locator('#estimateButton').click()
    expect(page.locator('#resultBody')).to_contain_text('검토 필요', timeout=30000)
    page.locator('#undoButton').click()

    # New preview does not mutate the old saved observation snapshot.
    assert client.get(f'/api/videos/{video_id}/registrations').json()[-1]['points'] == saved['points']
    page.locator('#estimateButton').click()
    expect(page.locator('#resultBody')).to_contain_text('사용 가능', timeout=30000)

    # Frame navigation must invalidate a response even if both drafts have
    # the same revision number. Returning to the frame retains its input.
    held.clear()
    page.route('**/registrations/preview', hold_preview)
    page.locator('#estimateButton').click()
    wait_held(page, held)
    page.locator('#nextFrame').click()
    ready(page)
    for route, response in held:
        route.fulfill(response=response)
    page.unroute('**/registrations/preview', hold_preview)
    expect(page.locator('#pointCount')).to_have_text('0')
    expect(page.locator('#resultBody')).to_have_attribute('data-kind', 'empty')
    expect(page.locator('#estimateButton')).to_be_disabled()
    page.locator('#previousFrame').click()
    ready(page)
    expect(page.locator('#estimateButton')).to_be_enabled()

    # Selecting an existing result on the same frame also cancels a pending
    # preview and restores a saved snapshot, without that reply replacing it.
    held.clear()
    page.route('**/registrations/preview', hold_preview)
    page.locator('#estimateButton').click()
    wait_held(page, held)
    page.locator('#historyList .history-item').first.click()
    for route, response in held:
        route.fulfill(response=response)
    page.unroute('**/registrations/preview', hold_preview)
    expect(page.locator('#resultBody')).to_have_attribute('data-kind', 'saved')
    expect(page.locator('#estimateButton')).to_be_enabled()
    page.locator('#toolSelect').click()
    page.locator('#independentValidationToggle').uncheck()
    page.locator('#sceneBrowser summary').click()
    first_thumbnail = page.locator('#thumbnailStrip img').first
    expect(first_thumbnail).to_be_visible()
    page.wait_for_function('document.querySelector("#thumbnailStrip img").naturalWidth > 0')
    page.locator('#thumbnailStrip button').nth(1).click()
    ready(page)
    expect(page.locator('#sourceCanvas')).to_have_attribute('data-frame-index', '7')
    page.locator('#historyList .history-item').first.click()
    ready(page)
    page.screenshot(path=str(ARTIFACTS/'editor-desktop.png'), full_page=True)

    # Overlay toggles must actually change pixels on the original-frame canvas.
    canvas = page.locator('#sourceCanvas')
    enabled = canvas.evaluate('c=>c.toDataURL()')
    page.locator('#independentValidationToggle').check()
    independent = canvas.evaluate('c=>c.toDataURL()')
    assert enabled != independent, 'Independent validation did not hide predictive overlay'
    page.locator('#independentValidationToggle').uncheck()
    page.locator('#overlayLinesToggle').uncheck()
    page.locator('#overlaySupportToggle').uncheck()
    page.locator('#overlayResidualToggle').uncheck()
    disabled = canvas.evaluate('c=>c.toDataURL()')
    assert enabled != disabled, 'Source overlay did not affect source canvas pixels'
    assert independent == disabled, 'Independent validation retained a predictive source overlay'
    page.set_viewport_size({'width':390,'height':844})
    page.screenshot(path=str(ARTIFACTS/'editor-mobile.png'), full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), 'Mobile horizontal overflow'
    # This is the generated fixture, never a user video. Even a browser-cached
    # adjacent frame must revalidate its source before showing old pixels.
    with video.open('ab') as changed_source:
        changed_source.write(b'changed-fixture')
    page.locator('#nextFrame').click()
    expect(page.locator('#globalAlertText')).to_contain_text('changed after it was indexed')
    expect(page.locator('#sourceCanvasMessage')).to_be_visible()
    assert not errors, errors
    return video_id


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='gsr-browser-') as temp:
        path = Path(os.environ.get('GSR_TEST_VIDEO_PATH', str(Path(temp)/'synthetic.mp4')))
        make_video(path)
        with sync_playwright() as p, httpx.Client(base_url=BASE, timeout=60) as client:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={'width':1600,'height':1100}, device_scale_factor=2)
            page = context.new_page()
            try:
                exercise(page, path, client)
            except BaseException:
                page.screenshot(path=str(ARTIFACTS/'editor-failure.png'), full_page=True)
                raise
            finally:
                browser.close()
    print('PASS: preview no writes, native zoom/drag, undo/redo, frame drafts, stale response guard, source overlays, DPR2 and mobile')


if __name__ == '__main__':
    main()
