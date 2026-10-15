# 最终协调者证据索引

以 ../../skill-tool-selection-final-acceptance.md 为当前结论；历史失败、错误harness与中间输出保留，不把中间文件当最终报告。所有账号、问题、MCP响应是专用合成验收数据。真实配置密钥、会话、dump不在此目录。

最终来源与时序：incoming.json / final-source-fingerprint.json / phase-timing.json。

- 最终全量：admin-delivery.txt、root-delivery-final.txt、integration-final.txt、frontend-contracts-stop-final.txt、frontend-build-stop-final.txt、tsc-stop-final.txt、ruff-final.txt、openspec-final.txt。
- 六图实际原模型：api-matrix-combined.json / matrix-ledger-observation-proof.json（36格）；deep-explicit-fixture.json（额外6条S1）；skill-stage-real-proof.json（12条S1实际材料与各阶段）；interrupt-resume-combined.json（12恢复）。
- SDK+受控模型+实际MCP网络：sdk-dispatch.json、business-dispatch.json、business-disclosure-verification.json、full-protection-benchmark.json。不要称生产平台MCP已验证。
- 分离进程：process-boundary.json；迁移：migrations.txt；租户隔离：tenant-rls-pytest.txt；真实备份恢复：restore-deletion.json / restore-final.txt。
- 真实UI：browser-evidence.json及相应PNG；最终停止：browser-stop-final.json。OS中文输入法候选窗未实测，Composer事件测试不代替这一项。
- 清理：temporary-skill-cleanup.json / resource-cleanup.json / sdk-harness-process-cleanup.json。

复验先核实容器/库所有权，禁止把任何TRUNCATE夹具指向共享15432。专项测试入口分别在admin/api/tests/test_ledger_selection_persistence.py、test_capability_restore_live.py及tests/unit_tests/test_deep_explicit_skill.py；前端使用npm run test:contracts。run-tests.py保存本轮合成环境参数，实际执行的共享Python环境位于/tmp/aa-takeover35-py313，临时路径不保证长期存在。

复现原模型矩阵或恢复脚本需重新建立自己的API/管理端/BFF，提供私有/tmp/aa05f-stack-env.json及browser-identity.json（不入库），确保官方API真实启用自定义auth。Deep原目录只有.gitkeep，重跑Deep S1时须从deep-fixture-materials.json恢复临时材料后再删；不能直接把本报告临时材料长期加入业务目录。verify-skill-stages.py读取已成功运行的安全ID，需要专用测试库保留相应合成记录。脚本中phase proof会重放确认，不创建新运行。

benchmark-sdk-final.py和独立SDK dispatch脚本断言完成、fixture停止、JSON写入后，部分Python父进程仍等待后台资源；按cleanup记录显式终止。这一退出问题不隐藏为exit0，JSON仅证明其实际完成的断言与采样。
