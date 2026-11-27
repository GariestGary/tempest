import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ActionMenu, Select } from '../WorkingCoreControls'

describe('Working Core controls', () => {
  it('supports keyboard selection, escape and native form submission', () => {
    const submit = vi.fn((event: React.FormEvent<HTMLFormElement>) => event.preventDefault())
    render(<form onSubmit={submit}><Select aria-label="Провайдер" name="provider" defaultValue="one"><option value="one">Первый</option><option value="two">Второй</option></Select><button>Сохранить</button></form>)
    const combo = screen.getByRole('combobox', { name: 'Провайдер' })
    combo.focus()
    fireEvent.keyDown(combo, { key: 'ArrowDown' })
    expect(combo).toHaveAttribute('aria-expanded', 'true')
    fireEvent.keyDown(combo, { key: 'ArrowDown' })
    fireEvent.keyDown(combo, { key: 'Enter' })
    expect(combo).toHaveTextContent('Второй')
    expect(combo).toHaveFocus()
    expect(new FormData(combo.closest('form')!).get('provider')).toBe('two')
    fireEvent.keyDown(combo, { key: 'ArrowDown' })
    fireEvent.keyDown(combo, { key: 'Escape' })
    expect(combo).toHaveAttribute('aria-expanded', 'false')
    fireEvent.reset(combo.closest('form')!)
    expect(combo).toHaveTextContent('Первый')
  })
  it('moves focus in action menus and restores it on escape', () => {
    render(<ActionMenu label="Действия" actions={[{ label: 'Выбрать', onSelect: vi.fn() }, { label: 'Удалить', onSelect: vi.fn() }]}/>)
    const trigger = screen.getByRole('button', { name: 'Действия' })
    fireEvent.click(trigger)
    const menu = screen.getByRole('menu')
    const items = within(menu).getAllByRole('menuitem')
    expect(items[0]).toHaveFocus()
    fireEvent.keyDown(items[0], { key: 'ArrowDown' })
    expect(items[1]).toHaveFocus()
    fireEvent.keyDown(items[1], { key: 'Escape' })
    expect(screen.queryByRole('menu')).toBeNull()
    expect(trigger).toHaveFocus()
  })
})
