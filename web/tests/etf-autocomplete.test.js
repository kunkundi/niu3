import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { createRenderer, h, nextTick, ref } from 'vue'
import { compileScript, parse } from 'vue/compiler-sfc'

import { state, cancelAuthentication, completeAuthentication } from '../src/state.js'

// Compile the actual dialog and children so tests cover their real event wiring.
const modules = {}
for (const name of ['EtfAutocomplete', 'WorkbenchDialog', 'AddEtfDialog']) {
  const source = await readFile(new URL(`../src/components/${name}.vue`, import.meta.url), 'utf8')
  const { descriptor } = parse(source)
  const compiled = compileScript(descriptor, { id: `etf-test-${name}`, inlineTemplate: true })
  const script = compiled.content.replace(/from (['"])([^'"]+)\1/g, (_, quote, specifier) => {
    const url =
      specifier === 'vue'
        ? import.meta.resolve('vue')
        : specifier.endsWith('.vue')
          ? modules[specifier.slice(2, -4)]
          : new URL(`../src/${specifier.slice(3)}${specifier.endsWith('.js') ? '' : '.js'}`, import.meta.url)
              .href
    return `from ${JSON.stringify(url)}`
  })
  modules[name] = `data:text/javascript;base64,${Buffer.from(script).toString('base64')}`
}
const { default: EtfAutocomplete } = await import(modules.EtfAutocomplete)
const { default: AddEtfDialog } = await import(modules.AddEtfDialog)
const element = (type) => ({
  type,
  props: {},
  children: [],
  focus() {},
  showModal() {
    this.open = true
  },
  close() {
    this.open = false
  },
  querySelector() {
    return null
  },
})
function remove(node) {
  const siblings = node.parentElement?.children
  if (siblings) siblings.splice(siblings.indexOf(node), 1)
}
const renderer = createRenderer({
  createElement: element,
  createText: (text) => ({ type: '#text', text }),
  createComment: (text) => ({ type: '#comment', text }),
  setText: (node, text) => {
    node.text = text
  },
  setElementText: (node, text) => {
    node.text = text
    node.children = []
  },
  patchProp: (node, key, previous, value) => {
    node.props[key] = value
  },
  insert(node, parent, anchor) {
    remove(node)
    node.parentElement = parent
    const index = parent.children.indexOf(anchor)
    parent.children.splice(index < 0 ? parent.children.length : index, 0, node)
  },
  remove,
  parentNode: (node) => node.parentElement,
  nextSibling: (node) => node.parentElement?.children[node.parentElement.children.indexOf(node) + 1],
})
function find(node, type) {
  return node.type === type ? node : node.children?.map((child) => find(child, type)).find(Boolean)
}
const item = { symbol: 'sh588170', name: '科创半导体ETF华夏', watched: false }
const label = '科创半导体ETF华夏 (588170)'

const settle = async () => {
  await new Promise((resolve) => setTimeout(resolve, 280))
  await nextTick()
}
const key = (input, value, extra = {}) => input.props.onKeydown({ key: value, preventDefault() {}, ...extra })
function findWhere(node, predicate) {
  return predicate(node) ? node : node.children?.map((child) => findWhere(child, predicate)).find(Boolean)
}
const button = (root, text) => findWhere(root, (node) => node.type === 'button' && node.text?.trim() === text)

test('mouse and keyboard only select; Enter never submits free text or a selected ETF', async (t) => {
  const requests = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push(options.method || 'GET')
    return new Response(JSON.stringify({ items: [item] }))
  })
  for (const method of ['mouse', 'keyboard']) {
    const model = ref(''),
      busy = ref(false),
      selections = [],
      submissions = [],
      root = element('root')
    const app = renderer.createApp({
      setup: () => () =>
        h(EtfAutocomplete, {
          modelValue: model.value,
          disabled: busy.value,
          'onUpdate:modelValue': (value) => {
            model.value = value
          },
          onSelect: (value) => selections.push(value),
          onSubmit: () => submissions.push(model.value),
        }),
    })
    app.mount(root)
    try {
      const input = find(root, 'input')
      input.props.onInput({ target: { value: '588170' } })
      key(input, 'Enter')
      assert.equal(model.value, '588170')
      assert.equal(selections.at(-1), null)
      await settle()
      const option = find(root, 'li')
      assert.ok(option)
      key(input, 'ArrowDown')
      key(input, 'Enter', { isComposing: true })
      key(input, 'Enter', { keyCode: 229 })
      assert.equal(selections.at(-1), null, 'IME Enter cannot select an ETF')
      if (method === 'mouse') option.props.onClick()
      else key(input, 'Enter')
      assert.equal(model.value, item.symbol)
      assert.deepEqual(selections.at(-1), item)
      await nextTick()
      assert.equal(input.props.value, label)
      input.props.onCompositionend({ target: { value: label } })
      input.props.onInput({ target: { value: label } })
      key(input, 'Enter')
      key(input, 'Enter')
      assert.deepEqual(submissions, [])
      assert.equal(find(root, 'li'), undefined)
      assert.deepEqual(selections.at(-1), item, 'IME trailing input preserves the selection')
      busy.value = true
      await nextTick()
      const count = selections.length
      option.props.onClick()
      key(input, 'Enter')
      assert.equal(selections.length, count)
      busy.value = false
      await nextTick()
      input.props.onInput({ target: { value: '159915' } })
      assert.equal(model.value, '159915')
      assert.equal(selections.at(-1), null, 'editing clears the selection synchronously')
      model.value = ''
      await nextTick()
      assert.equal(input.props.value, '')
    } finally {
      app.unmount()
    }
  }
  assert.ok(requests.every((method) => method === 'GET'))
})

test('already added suggestions cannot be selected with mouse or keyboard', async (t) => {
  t.mock.method(
    globalThis,
    'fetch',
    async () => new Response(JSON.stringify({ items: [{ ...item, watched: true }] })),
  )
  const root = element('root'),
    selections = []
  const app = renderer.createApp({
    setup: () => () => h(EtfAutocomplete, { onSelect: (value) => selections.push(value) }),
  })
  app.mount(root)
  try {
    const input = find(root, 'input')
    input.props.onInput({ target: { value: '588170' } })
    await settle()
    const option = find(root, 'li')
    assert.equal(option.props['aria-disabled'], true)
    option.props.onClick()
    key(input, 'ArrowDown')
    key(input, 'Enter')
    assert.equal(selections.at(-1), null)
  } finally {
    app.unmount()
  }
})

function mountDialog() {
  const root = element('root'),
    shown = ref(true),
    added = []
  const app = renderer.createApp({
    setup: () => () =>
      shown.value
        ? h(AddEtfDialog, {
            onClose: () => {
              shown.value = false
            },
            onAdded: (result) => {
              added.push(result)
              shown.value = false
            },
          })
        : null,
  })
  app.mount(root)
  return { root, app, shown, added }
}

test('dialog requires a fresh selection and explicit confirmation; rapid repeats send one request', async (t) => {
  const previousAuth = state.authenticated
  state.authenticated = true
  const posts = []
  let finish
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (options.method === 'POST') {
      posts.push(JSON.parse(options.body))
      return new Promise((resolve) => {
        finish = () => resolve(new Response(JSON.stringify({ message: '已添加' })))
      })
    }
    return new Response(JSON.stringify({ items: [item] }))
  })
  const { root, app, shown, added } = mountDialog()
  try {
    const input = find(root, 'input'),
      confirm = button(root, '确认添加')
    assert.equal(confirm.props.type, 'button', 'no implicit form submit')
    assert.equal(confirm.props.disabled, true)
    input.props.onInput({ target: { value: '588170' } })
    key(input, 'Enter')
    await confirm.props.onClick()
    assert.deepEqual(posts, [], 'typing a valid code does not authorize adding')
    await settle()
    find(root, 'li').props.onClick()
    await nextTick()
    assert.equal(confirm.props.disabled, false)
    assert.ok(findWhere(root, (node) => node.text === item.name))
    key(input, 'Enter')
    assert.deepEqual(posts, [], 'choosing and Enter do not add')
    input.props.onInput({ target: { value: '159915' } })
    await confirm.props.onClick()
    assert.deepEqual(posts, [], 'editing invalidates the old selection before render')
    await settle()
    find(root, 'li').props.onClick()
    await nextTick()
    const pending = confirm.props.onClick()
    const repeated = confirm.props.onClick()
    await nextTick()
    assert.deepEqual(posts, [{ code: item.symbol }])
    assert.equal(confirm.props.disabled, true)
    assert.equal(button(root, '取消').props.disabled, true)
    assert.equal(button(root, '关闭').props.disabled, true)
    let prevented = false
    find(root, 'dialog').props.onCancel({
      preventDefault() {
        prevented = true
      },
    })
    assert.equal(prevented, true, 'Escape cannot close a pending addition')
    finish()
    await Promise.all([pending, repeated])
    await nextTick()
    assert.deepEqual(added, [{ message: '已添加' }])
    assert.equal(shown.value, false)
    shown.value = true
    await nextTick()
    assert.equal(find(root, 'input').props.value, '', 'reopening starts with no stale selection')
    assert.equal(button(root, '确认添加').props.disabled, true)
    button(root, '取消').props.onClick()
    await nextTick()
    assert.equal(shown.value, false)
    assert.equal(posts.length, 1)
  } finally {
    app.unmount()
    state.authenticated = previousAuth
  }
})

test('cancelled authentication and failed additions keep the dialog recoverable without an unintended add', async (t) => {
  const previousAuth = state.authenticated
  state.authenticated = false
  let posts = 0
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (options.method === 'POST') {
      posts++
      return new Response(JSON.stringify({ detail: '无法核验此 ETF，请重试' }), { status: 422 })
    }
    return new Response(JSON.stringify({ items: [item] }))
  })
  const { root, app, shown, added } = mountDialog()
  try {
    find(root, 'input').props.onInput({ target: { value: '588170' } })
    await settle()
    find(root, 'li').props.onClick()
    await nextTick()
    const confirm = button(root, '确认添加')
    const cancelled = confirm.props.onClick()
    assert.equal(state.authRequested, true)
    assert.equal(posts, 0)
    cancelAuthentication()
    await cancelled
    await nextTick()
    assert.equal(shown.value, true)
    assert.equal(confirm.props.disabled, false)
    assert.ok(findWhere(root, (node) => node.text?.includes('已取消操作')))
    const retry = confirm.props.onClick()
    completeAuthentication()
    await retry
    await nextTick()
    assert.equal(posts, 1)
    assert.deepEqual(added, [])
    assert.equal(shown.value, true)
    assert.ok(findWhere(root, (node) => node.text?.includes('无法核验此 ETF')))
    assert.equal(button(root, '取消').props.disabled, false)
  } finally {
    app.unmount()
    cancelAuthentication()
    state.authenticated = previousAuth
  }
})
