import { Children, isValidElement, useEffect, useId, useRef, useState, type ReactElement, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronDown, MoreHorizontal } from 'lucide-react'

type OptionProps = { value?: string; disabled?: boolean; children?: ReactNode }
type SelectProps = {
  children: ReactNode; name?: string; value?: string; defaultValue?: string; disabled?: boolean; required?: boolean; className?: string; 'aria-label': string
  onChange?: (event: { target: { value: string } }) => void
}

export function Select({ children, name, value, defaultValue, disabled, required, onChange, 'aria-label': label }: SelectProps) {
  const options = Children.toArray(children).filter(isValidElement) as ReactElement<OptionProps>[]
  const [internal, setInternal] = useState(defaultValue ?? options[0]?.props.value ?? '')
  const selected = value ?? internal
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const [invalid, setInvalid] = useState(false)
  const [position, setPosition] = useState({ top: 0, left: 0, width: 0, maxHeight: 240 })
  const root = useRef<HTMLSpanElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const native = useRef<HTMLSelectElement>(null)
  const list = useRef<HTMLDivElement>(null)
  const id = useId()
  const choices = options.map(item => ({ value: String(item.props.value ?? item.props.children ?? ''), label: item.props.children, disabled: Boolean(item.props.disabled) }))
  const current = choices.findIndex(item => item.value === selected)
  const enabled = choices.map((item, index) => item.disabled ? -1 : index).filter(index => index >= 0)
  const panelTarget = trigger.current?.closest('dialog[open]') ?? document.body
  function place() {
    const rect = trigger.current?.getBoundingClientRect()
    if (!rect) return
    const roomBelow = window.innerHeight - rect.bottom - 12
    const height = Math.min(240, Math.max(roomBelow >= 120 ? roomBelow : rect.top - 12, 80))
    setPosition({ left: Math.max(8, Math.min(rect.left, window.innerWidth - rect.width - 8)), top: roomBelow >= 120 ? rect.bottom + 5 : Math.max(8, rect.top - height - 5), width: rect.width, maxHeight: height })
  }
  function show() { if (disabled || !enabled.length) return; place(); setActive(current >= 0 && !choices[current].disabled ? current : enabled[0]); setOpen(true) }
  function choose(index: number) {
    const choice = choices[index]
    if (!choice || choice.disabled) return
    setInvalid(false)
    if (value === undefined) setInternal(choice.value)
    onChange?.({ target: { value: choice.value } })
    native.current?.dispatchEvent(new Event('change', { bubbles: true }))
    setOpen(false)
    trigger.current?.focus()
  }
  useEffect(() => {
    const form = native.current?.form
    const reset = () => { setInternal(defaultValue ?? options[0]?.props.value ?? ''); setInvalid(false); setOpen(false) }
    form?.addEventListener('reset', reset)
    return () => form?.removeEventListener('reset', reset)
  // options are supplied by the parent; reset should use the initial default.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defaultValue])
  useEffect(() => {
    if (!open) return
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node) && !list.current?.contains(event.target as Node)) setOpen(false) }
    const reposition = () => place()
    document.addEventListener('pointerdown', outside)
    window.addEventListener('resize', reposition)
    window.addEventListener('scroll', reposition, true)
    return () => { document.removeEventListener('pointerdown', outside); window.removeEventListener('resize', reposition); window.removeEventListener('scroll', reposition, true) }
  }, [open])
  function keyboard(event: React.KeyboardEvent<HTMLButtonElement>) {
    if (event.key === 'Escape' && open) { event.preventDefault(); event.stopPropagation(); setOpen(false); return }
    if (event.key === 'Tab') { setOpen(false); return }
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault()
      if (!open) { show(); return }
      const at = enabled.indexOf(active)
      const next = event.key === 'Home' ? enabled[0] : event.key === 'End' ? enabled[enabled.length - 1] : enabled[(at + (event.key === 'ArrowDown' ? 1 : -1) + enabled.length) % enabled.length]
      setActive(next); document.getElementById(`${id}-${next}`)?.scrollIntoView?.({ block: 'nearest' }); return
    }
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); if (open) choose(active); else show() }
  }
  return <span className="wc-select" ref={root}>
    <select ref={native} className="wc-native-select" tabIndex={-1} aria-hidden="true" name={name} value={selected} disabled={disabled} required={required} onChange={() => {}} onInvalid={event => { event.preventDefault(); setInvalid(true); trigger.current?.focus() }}>{children}</select>
    <button ref={trigger} type="button" role="combobox" aria-label={label} aria-expanded={open} aria-controls={id} aria-activedescendant={open ? `${id}-${active}` : undefined} aria-required={required} aria-invalid={invalid || undefined} aria-describedby={invalid ? `${id}-error` : undefined} disabled={disabled} className="wc-select-trigger" onClick={() => open ? setOpen(false) : show()} onKeyDown={keyboard}>
      <span>{choices[current]?.label ?? 'Выберите'}</span><ChevronDown size={16}/>
    </button>
    {invalid && <span id={`${id}-error`} className="wc-select-error">Выберите значение.</span>}
    {open && createPortal(<div ref={list} id={id} role="listbox" className="wc-popover wc-options" style={{ position: 'fixed', ...position }}>
      {choices.map((item, index) => <button id={`${id}-${index}`} type="button" tabIndex={-1} role="option" aria-selected={index === current} disabled={item.disabled} className={index === active ? 'is-active' : ''} key={item.value} onPointerMove={() => !item.disabled && setActive(index)} onClick={() => choose(index)}><span>{item.label}</span>{index === current && <Check size={15}/>}</button>)}
    </div>, panelTarget)}
  </span>
}

type MenuAction = { label: string; disabled?: boolean; danger?: boolean; onSelect: () => void }
export function ActionMenu({ label, actions }: { label: string; actions: MenuAction[] }) {
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState({ top: 0, left: 0 })
  const trigger = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const target = trigger.current?.closest('dialog[open]') ?? document.body
  function show() { const rect = trigger.current?.getBoundingClientRect(); if (!rect) return; setPosition({ top: Math.min(rect.bottom + 5, window.innerHeight - 170), left: Math.max(8, rect.right - 210) }); setOpen(true) }
  useEffect(() => {
    if (!open) return
    menu.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus()
    const outside = (event: PointerEvent) => { if (!menu.current?.contains(event.target as Node) && !trigger.current?.contains(event.target as Node)) setOpen(false) }
    const close = () => setOpen(false)
    document.addEventListener('pointerdown', outside); window.addEventListener('scroll', close, true); window.addEventListener('resize', close)
    return () => { document.removeEventListener('pointerdown', outside); window.removeEventListener('scroll', close, true); window.removeEventListener('resize', close) }
  }, [open])
  return <><button ref={trigger} type="button" className="wc-menu-trigger" aria-label={label} aria-haspopup="menu" aria-expanded={open} onClick={() => open ? setOpen(false) : show()}><MoreHorizontal size={19}/></button>
    {open && createPortal(<div ref={menu} role="menu" aria-label={label} className="wc-popover wc-menu-popover" style={{ position: 'fixed', ...position }} onKeyDown={event => {
      if (event.key === 'Escape') { event.stopPropagation(); setOpen(false); trigger.current?.focus() }
      if (event.key === 'Tab') setOpen(false)
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); const buttons = Array.from(menu.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? []); const index = buttons.indexOf(document.activeElement as HTMLButtonElement); buttons[(index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length]?.focus() }
    }}>{actions.map(action => <button type="button" role="menuitem" disabled={action.disabled} className={action.danger ? 'is-danger' : ''} key={action.label} onClick={() => { setOpen(false); trigger.current?.focus(); action.onSelect() }}>{action.label}</button>)}</div>, target)}
  </>
}

export function DecisionDialog({ title, body, initial, onCancel, onConfirm }: {
  title: string; body: string; initial?: string; onCancel: () => void; onConfirm: (value: string) => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [text, setText] = useState(initial ?? '')
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    const element = dialog.current
    if (element?.showModal) element.showModal()
    else element?.setAttribute('open', '')
    return () => { previous?.focus() }
  }, [])
  return <dialog ref={dialog} className="wc-dialog" aria-label={title} onCancel={event => { event.preventDefault(); onCancel() }}>
    <form onSubmit={event => { event.preventDefault(); onConfirm(text.trim()) }}>
      <div className="wc-dialog-head"><h2>{title}</h2><button type="button" className="wc-dialog-close" aria-label="Закрыть" onClick={onCancel}>×</button></div>
      <div className="wc-dialog-content"><p>{body}</p>{initial !== undefined && <label className="wc-decision-input">Название<input autoFocus required maxLength={64} value={text} onChange={event => setText(event.target.value)} /></label>}
        <div className="wc-decision-actions"><button type="button" className="wc-secondary-button" onClick={onCancel} autoFocus={initial === undefined}>Отмена</button><button type="submit" className="wc-decision-confirm" disabled={initial !== undefined && !text.trim()}>{initial === undefined ? 'Подтвердить' : 'Сохранить'}</button></div>
      </div>
    </form>
  </dialog>
}
