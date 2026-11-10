import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import CardFields from '@/components/CardFields.vue'
import type { CharacterCard } from '@/api/types'

function makeCard(overrides: Partial<CharacterCard> = {}): CharacterCard {
  return {
    schema_version: '1.0',
    name: '灰烬旅人',
    attributes: { str: 12, dex: 14, con: 11, int: 15, wis: 10, cha: 8, max_hp: 30 },
    hp: 30,
    starting_items: ['item.torch', 'item.potion'],
    background: '在旧矿镇长大的拾荒者。',
    appearance: null,
    source: 'upload',
    generated_by: null,
    ...overrides,
  }
}

describe('CardFields（详情页 9 字段只读渲染）', () => {
  it('渲染 9 字段：名称/HP/属性中文化/初始物品/来源/生成者/schema 版本', () => {
    const wrapper = mount(CardFields, { props: { card: makeCard() } })

    expect(wrapper.text()).toContain('灰烬旅人')
    expect(wrapper.text()).toContain('30 / 30')
    expect(wrapper.text()).toContain('schema 版本')
    expect(wrapper.text()).toContain('1.0')
    expect(wrapper.text()).toContain('上传') // source: upload
    expect(wrapper.text()).toContain('火把、治疗药水') // P1：模板键 item.torch/item.potion 中文化
    expect(wrapper.text()).toContain('力量 12')
    expect(wrapper.text()).toContain('生命上限 30')
    expect(wrapper.text()).toContain('在旧矿镇长大的拾荒者。')
  })

  it('P1 初始物品：模板键中文化（item. 与 item_ 写法等价），未知键回退原值', () => {
    const wrapper = mount(CardFields, {
      props: {
        card: makeCard({
          starting_items: ['item.torch', 'item_rusty_knife', 'item.custom_future'],
        }),
      },
    })

    expect(wrapper.text()).toContain('火把、锈刀、item.custom_future')
    expect(wrapper.text()).not.toContain('item.torch')
    expect(wrapper.text()).not.toContain('item_rusty_knife')

    const empty = mount(CardFields, { props: { card: makeCard({ starting_items: [] }) } })
    expect(empty.text()).toContain('（无）')
  })

  it('可空字段与生成者回退「（无）」', () => {
    const wrapper = mount(CardFields, {
      props: { card: makeCard({ appearance: null, generated_by: null }) },
    })

    expect(wrapper.text()).toContain('（无）')
  })
})
