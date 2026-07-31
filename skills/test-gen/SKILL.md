---
name: test-gen
description: 自动生成测试用例 - 覆盖正常/边界/异常路径
allowed-tools: read_file, write_file, replace_in_file, apply_patch, run_shell, grep, todo_write
---

# 测试生成

为指定代码自动生成全面的测试用例。

## 工作流程

### 第一步：分析目标代码
1. 用 `read_file` 读取目标文件，理解函数签名、参数、返回值
2. 用 `grep` 查找现有测试文件和测试模式
3. 确认测试框架和运行命令（检查 package.json / pyproject.toml / Makefile 等）
4. 了解项目的 mock 策略、fixture 定义、helper 函数

### 第二步：设计测试用例

为每个公开函数/方法，按以下矩阵设计用例：

**正常路径（Happy Path）**
- 典型输入 -> 期望输出
- 多种合法输入组合
- 默认参数行为
- 返回值类型和结构验证

**边界条件（Boundary）**
- 空输入：空字符串、空数组、空对象、None/null
- 单元素：长度为 1 的集合
- 极值：0、-1、MAX_INT、超大字符串、深嵌套
- 边界：恰好满足/不满足条件（off-by-one）

**异常路径（Error Path）**
- 非法输入类型：字符串传数字参数、None 传非空字段
- 业务规则违反：权限不足、状态冲突、资源不存在
- 外部依赖失败：网络超时、数据库连接断开、文件不存在
- 并发冲突：重复提交、竞态条件

**回归测试（Regression）**
- 检查 git_log 中该文件最近的 bug 修复
- 为每个已修复的 bug 生成复现测试

### 第三步：生成测试代码
1. 遵循项目现有的测试风格和命名规范
2. 使用 AAA 模式：Arrange（准备）、Act（执行）、Assert（断言）
3. 每个测试函数只测一个行为
4. 测试名描述行为而非实现：`test_空用户名抛出ValueError` 而非 `test_case1`
5. 使用项目的 fixture 和 helper，不重复造轮子

### 第四步：验证
1. 运行生成的测试，确认全部通过
2. 检查覆盖率：`pytest --cov` / `npm test -- --coverage`
3. 临时破坏实现代码，确认测试会失败（否则测试无意义）

## 输出规范
- 测试文件放在项目约定的位置（tests/ / __tests__/ / *_test.go 等）
- 如果目标文件已有测试文件，追加用例；否则创建新文件
- 在每个测试组前加注释说明覆盖的场景类别
