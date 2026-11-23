import { expect, test } from '@playwright/test'
import path from 'node:path'

const sample = (name: string) => path.resolve(import.meta.dirname, '../../samples', name)

test('5,000-character text opens and re-paginates quickly', async ({ page }) => {
  await page.goto('/')
  await page.getByTestId('import-input').setInputFiles(sample('sample-long.json'))
  await expect(page.getByTestId('library-item')).toHaveCount(1)

  const link = page.getByTestId('library-item').getByRole('link')
  const t0 = Date.now()
  await link.click()
  await expect(page.getByTestId('page-count')).toContainText(' of ')
  const open = Date.now() - t0

  await page.getByRole('button', { name: 'Display settings' }).click()
  const before = await page.getByTestId('page-count').textContent()
  const t1 = Date.now()
  await page.getByLabel(/Text size/).fill('34')
  await expect(page.getByTestId('page-count')).not.toHaveText(before ?? '')
  const repaginate = Date.now() - t1

  console.log(`open=${open}ms repaginate=${repaginate}ms`)
  // desktop-class budgets; a mid-range phone is a few times slower
  expect(open).toBeLessThan(1500)
  expect(repaginate).toBeLessThan(1500)
})
