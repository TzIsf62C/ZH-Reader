import { expect, test, type Page } from '@playwright/test'
import path from 'node:path'

const sample = (name: string) => path.resolve(import.meta.dirname, '../../samples', name)

async function importFiles(page: Page, ...names: string[]) {
  await page.goto('/')
  await page.getByTestId('import-input').setInputFiles(names.map(sample))
}

async function pageCount(page: Page): Promise<{ page: number; total: number }> {
  const text = (await page.getByTestId('page-count').textContent()) ?? ''
  const m = text.match(/(\d+) of (\d+)/)
  return m ? { page: Number(m[1]), total: Number(m[2]) } : { page: -1, total: -1 }
}

async function swipeLeft(page: Page) {
  const box = (await page.getByTestId('page-area').boundingBox())!
  const y = box.y + box.height - 30
  await page.mouse.move(box.x + box.width - 25, y)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width / 2, y, { steps: 6 })
  await page.mouse.move(box.x + 30, y, { steps: 6 })
  await page.mouse.up()
}

async function openText(page: Page, title: RegExp) {
  await page.getByTestId('library-item').filter({ hasText: title }).getByRole('link').click()
  await expect(page.getByTestId('page-count')).toContainText(/of|End/)
}

async function setDisplay(page: Page, fn: () => Promise<void>) {
  await page.getByRole('button', { name: 'Display settings' }).click()
  await fn()
  await page.getByRole('button', { name: 'Done' }).click()
}

test('import, paginate, re-paginate on text size, highlight themes', async ({ page }) => {
  await importFiles(page, 'sample-long.json', 'sample-simplified.json')
  await expect(page.getByTestId('library-item')).toHaveCount(2)

  // unsupported version is rejected with a clear message
  await page.getByTestId('import-input').setInputFiles({
    name: 'bad.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify({ schemaVersion: 2 })),
  })
  await expect(page.getByText(/Unsupported schemaVersion \(2\)/)).toBeVisible()

  // re-importing prompts for a decision
  await page.getByTestId('import-input').setInputFiles(sample('sample-simplified.json'))
  await expect(page.getByRole('dialog', { name: 'Already in your library' })).toBeVisible()
  await page.getByRole('button', { name: 'Skip' }).click()
  await expect(page.getByTestId('library-item')).toHaveCount(2)

  await openText(page, /长文本/)
  const first = await pageCount(page)
  expect(first.page).toBe(1)
  expect(first.total).toBeGreaterThan(3)
  // the first sentence of the opened page is selected
  await expect(page.locator('.sent.sel')).toHaveCount(1)

  await swipeLeft(page)
  await expect.poll(async () => (await pageCount(page)).page).toBe(2)

  await setDisplay(page, () => page.getByLabel(/Text size/).fill('36'))
  const bigger = await pageCount(page)
  expect(bigger.total).toBeGreaterThan(first.total)
  await setDisplay(page, () => page.getByLabel(/Text size/).fill('24'))
  expect(await pageCount(page)).toEqual({ page: 2, total: first.total })

  // dark theme: dark gray sentence highlight
  await setDisplay(page, () => page.getByRole('button', { name: 'Dark' }).click())
  const bg = await page.locator('.sent.sel').first().evaluate((el) => getComputedStyle(el).backgroundColor)
  expect(bg).toBe('rgb(58, 58, 60)')
  await setDisplay(page, () => page.getByRole('button', { name: 'Light' }).click())
})

test('hold shows word info in the top panel; tap panel toggles translation; finish flow', async ({ page }) => {
  await importFiles(page, 'sample-simplified.json')
  await openText(page, /去银行/)

  const bank = page.locator('.columns .w', { hasText: /^银行$/ }).first()
  const box = (await bank.boundingBox())!
  const x = box.x + box.width / 2
  const y = box.y + box.height / 2

  await page.mouse.click(x, y)
  await expect(page.locator('.sent.sel')).toHaveCount(1)
  await expect(page.getByTestId('translation')).toHaveCount(0)

  await page.getByTestId('info-panel').click({ position: { x: 20, y: 100 } })
  await expect(page.getByTestId('translation')).toContainText('Today I went to the bank')
  await page.getByTestId('info-panel').click({ position: { x: 20, y: 100 } })
  await expect(page.getByTestId('translation')).toHaveCount(0)

  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.waitForTimeout(700)
  await expect(page.getByTestId('word-info')).toContainText('yínháng')
  await expect(page.getByTestId('hsk-badge')).toHaveText('HSK 3')
  await expect(page.getByTestId('word-status')).toContainText('New')
  const panelBox = (await page.getByTestId('info-panel').boundingBox())!
  expect(panelBox.y + panelBox.height).toBeLessThanOrEqual(box.y)
  await page.mouse.up()
  await expect(page.getByTestId('word-info')).toBeVisible()

  await page.getByRole('button', { name: 'Add to vocab' }).click()
  await expect(page.getByRole('button', { name: 'In vocab ✓' })).toBeVisible()

  for (let i = 0; i < 12 && !(await page.getByTestId('page-count').textContent())?.includes('End'); i++) {
    await page.keyboard.press('ArrowRight')
    await page.waitForTimeout(300)
  }
  await expect(page.getByTestId('page-count')).toHaveText('End')
  await page.getByRole('button', { name: 'Mark as finished' }).click()
  await expect(page.getByTestId('finish-message')).toContainText('Counted 39 words')

  await page.getByRole('button', { name: 'Mark as finished' }).click()
  await expect(page.getByRole('dialog')).toContainText('You already finished this on')
  await page.getByRole('button', { name: 'Cancel' }).click()

  await page.locator('.reader-footer').getByRole('button', { name: 'Back to library' }).click()
  await page.getByRole('link', { name: 'Stats' }).click()
  await expect(page.getByTestId('total-texts')).toHaveText('1')
  await expect(page.getByTestId('total-exposures')).toHaveText('39')
  await expect(page.getByTestId('goal-progress')).toContainText('1 of 3')

  await page.getByRole('link', { name: 'Vocab' }).click()
  await expect(page.getByTestId('vocab-item')).toHaveCount(1)
  await expect(page.getByTestId('vocab-item')).toContainText('银行')
})

test('reading position is saved and restored per text', async ({ page }) => {
  await importFiles(page, 'sample-long.json')
  await openText(page, /长文本/)
  await swipeLeft(page)
  await swipeLeft(page)
  await expect.poll(async () => (await pageCount(page)).page).toBe(3)
  await page.waitForTimeout(500) // position save is debounced
  await page.locator('.reader-footer').getByRole('button', { name: 'Back to library' }).click()
  await expect(page.getByTestId('library-item')).toContainText(/% read/)
  await openText(page, /长文本/)
  expect((await pageCount(page)).page).toBe(3)
})

test('works offline after first load', async ({ page, context, browserName }) => {
  test.skip(browserName !== 'chromium', 'service worker offline check runs in Chromium')
  await page.goto('/')
  await page.evaluate(async () => {
    await navigator.serviceWorker.ready
  })
  await page.reload()
  await page.waitForFunction(() => !!navigator.serviceWorker.controller)
  await context.setOffline(true)
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Library' })).toBeVisible()
  await context.setOffline(false)
})
