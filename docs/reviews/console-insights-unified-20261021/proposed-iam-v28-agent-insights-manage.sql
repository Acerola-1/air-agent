-- 提议，未授权，未应用到 IAM 仓库。
-- 目标文件名：V28__agent_insights_manage.sql
-- 当前 IAM 迁移头是 V27。本文件只登记目录，不授予企业、角色或 root。
SELECT set_config('app.is_platform', 'on', true);

INSERT INTO iam.permissions
    (id, code, name_zh, scope, grantable_to_tenants, resource_code, sort)
VALUES
    ('01930000-0000-4000-8000-000000000515', 'agent:insights:manage',
     '运营需求洞察', 'tenant', true, NULL, 515)
ON CONFLICT (code) DO NOTHING;
