import type { ColumnRole, MetaRelationship } from '@/types/datasource'

/**
 * 列在「当前 ER 关系」下的有效角色:把尚未保存的关系编辑叠加到持久化的 role 上,
 * 仅用于即时预览显示(ER 图标 / 表元数据徽章),**不修改底层 c.role** ——
 * role 真正落库只走「保存关系」端点(后端 replace_relationships 联动写 column_info)。
 *
 * 规则与后端 replace_relationships 完全一致,只在 外键 ↔ 维度 之间联动:
 * - 主键(primary_key):表自身标识,与有无外键指向无关,永不变动;
 * - 是某条边的 from_column → foreign_key;
 * - 持久化是 foreign_key、但已不再是任何边的 from_column → 退回 dimension。
 *
 * 注:列以 (表名, 列名) 匹配 —— 本系统表 id === 表名,关系边 from_table 存的就是表名。
 */
export function effectiveColumnRole(
  persistedRole: ColumnRole,
  tableName: string,
  columnName: string,
  relationships: MetaRelationship[],
): ColumnRole {
  if (persistedRole === 'primary_key') return 'primary_key'
  const isForeignKey = relationships.some(
    (r) => r.from_table === tableName && r.from_column === columnName,
  )
  if (isForeignKey) return 'foreign_key'
  if (persistedRole === 'foreign_key') return 'dimension'
  return persistedRole
}
