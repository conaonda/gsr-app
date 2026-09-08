"""Real Chromium UI smoke test; run with the local server on port 8765."""
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1600, 'height': 1100}, device_scale_factor=1)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://127.0.0.1:8765')
    page.locator('#videoPath').fill(str(ROOT / 'data' / 'synthetic-qa.mp4'))
    page.locator('#importButton').click()
    expect(page.locator('#analysisStage')).to_be_visible(timeout=30000)
    expect(page.locator('#sourceCanvasMessage')).to_be_hidden(timeout=30000)
    def add(image, pitch):
        canvas = page.locator('#sourceCanvas')
        canvas.scroll_into_view_if_needed()
        box = canvas.bounding_box()
        page.mouse.click(box['x'] + image[0] / 960 * box['width'], box['y'] + image[1] / 540 * box['height'])
        canvas = page.locator('#pitchCanvas')
        canvas.scroll_into_view_if_needed()
        dimensions = canvas.evaluate('(c) => ({w:c.width,h:c.height})')
        w,h = dimensions['w'],dimensions['h']
        px,py = max(36,w*.075),max(32,h*.09)
        box = canvas.bounding_box()
        page.mouse.click(box['x'] + (px+pitch[0]*(w-2*px))/w*box['width'],
                         box['y'] + (py+pitch[1]*(h-2*py))/h*box['height'])
    for a,b in [([100,80],[0,0]),([860,80],[1,0]),([860,460],[1,1]),([100,460],[0,1])]:
        add(a,b)
    page.locator('#saveButton').click()
    expect(page.locator('#resultBody')).to_contain_text('검토 필요', timeout=30000)
    page.locator('[data-mode="validation"]').click()
    add([480,80],[.5,0])
    add([480,460],[.5,1])
    expect(page.locator('#pointCount')).to_have_text('6')
    page.locator('#saveButton').click()
    expect(page.locator('#resultBody')).to_contain_text('사용 가능', timeout=30000)
    page.screenshot(path=str(ROOT/'data'/'browser-qa.png'), full_page=True)
    # Changing field metadata must invalidate the displayed saved quality.
    page.locator('#fieldLength').fill('60')
    page.locator('#fieldWidth').fill('40')
    page.locator('#dimensionSource').fill('Synthetic verified field')
    page.locator('#dimensionsVerified').check()
    expect(page.locator('#resultBody')).not_to_contain_text('사용 가능')
    page.locator('#saveButton').click()
    expect(page.locator('#resultBody')).to_contain_text('사용 가능', timeout=30000)
    # Reopening the old normalized snapshot must restore unknown dimensions.
    page.locator('.history-item').nth(1).click()
    expect(page.locator('#fieldLength')).to_have_value('')
    expect(page.locator('#dimensionsVerified')).not_to_be_checked()
    expect(page.locator('#sourceCanvasMessage')).to_be_hidden(timeout=30000)
    page.locator('#propagationTarget').fill('1')
    page.locator('#propagateButton').click()
    expect(page.locator('#frameIndexReadout')).to_have_text('0001', timeout=30000)
    expect(page.locator('#sourceCanvasMessage')).to_be_hidden(timeout=30000)
    expect(page.locator('#pointCount')).to_have_text('4')
    expect(page.locator('#resultBody')).not_to_contain_text('사용 가능')
    page.locator('#nextFrame').click()
    expect(page.locator('#pointCount')).to_have_text('0')
    expect(page.locator('#frameIndexReadout')).to_have_text('0002')
    expect(page.locator('#sourceCanvasMessage')).to_be_hidden(timeout=30000)
    page.set_viewport_size({'width':390,'height':844})
    page.screenshot(path=str(ROOT/'data'/'browser-mobile-qa.png'), full_page=True)
    overflow = page.evaluate('''Array.from(document.querySelectorAll('body *'))
        .filter(e=>e.getBoundingClientRect().right>window.innerWidth+1)
        .map(e=>({tag:e.tagName,id:e.id,cls:e.className,right:e.getBoundingClientRect().right})).slice(0,20)''')
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), f'Mobile horizontal overflow: {overflow}'
    assert not errors, errors
    print('PASS: canvas pairs, registration, stale quality invalidation, historical field restore, propagation target, frame reset, mobile; no JS errors')
    browser.close()
