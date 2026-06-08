-- upload 库改名为 wenshu 的数据迁移。
--
-- 背景:db_upload 库历史名为 upload,实际装的是应用运营数据
--   (users / conversations / messages / upload_datasets / dataset_edit_*),
--   名不副实。配置已统一改为 wenshu,这里把已有数据搬过去。
--
-- 原理:MySQL 无 RENAME DATABASE;同实例 InnoDB 下 RENAME TABLE 跨库
--   是秒级元数据操作(不拷数据),最稳妥。
--
-- 用法(本地默认 root/root @ 3307):
--   mysql -h127.0.0.1 -P3307 -uroot -proot < scripts/migrate_upload_to_wenshu.sql
--
-- 注意:
--   * 若你的 db_upload 库名不是默认的 upload,先把下面 @src 改掉。
--   * 跑完确认 wenshu 库数据无误后,再手动 DROP 旧库(脚本末尾已注释,默认不删)。

CREATE DATABASE IF NOT EXISTS `wenshu` CHARACTER SET utf8mb4;

-- 逐表搬迁(只搬应用自有的 6 张表;upload 库里若有别的表不动)。
-- 若某张表不存在会报错并中断 —— 按需删掉对应行即可。
RENAME TABLE `upload`.`users`                 TO `wenshu`.`users`;
RENAME TABLE `upload`.`conversations`         TO `wenshu`.`conversations`;
RENAME TABLE `upload`.`messages`              TO `wenshu`.`messages`;
RENAME TABLE `upload`.`upload_datasets`       TO `wenshu`.`upload_datasets`;
RENAME TABLE `upload`.`dataset_edit_sessions` TO `wenshu`.`dataset_edit_sessions`;
RENAME TABLE `upload`.`dataset_edit_ops`      TO `wenshu`.`dataset_edit_ops`;

-- 验证:确认上面 6 张表都已出现在 wenshu 库
-- SHOW TABLES FROM `wenshu`;

-- 确认无误后再执行(默认注释,避免误删):
-- DROP DATABASE `upload`;
