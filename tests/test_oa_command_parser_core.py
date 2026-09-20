from karvyloop.channels.oa_command_parser import OACommand, parse_oa_command


def test_batch_command():
    assert parse_oa_command("2、5、8-10批准") == OACommand("approve", (2, 5, 8, 9, 10))


def test_reject_reason_and_forward_target():
    assert parse_oa_command("拒绝2，原因：材料不完整") == OACommand("reject", (2,), "材料不完整", "")
    assert parse_oa_command("转发3给张三") == OACommand("forward", (3,), "", "张三")


def test_recovery_commands_and_ambiguity():
    assert parse_oa_command("？") == OACommand("continue")
    assert parse_oa_command("刷新列表") == OACommand("refresh")
    assert parse_oa_command("重试") == OACommand("retry")
    assert parse_oa_command("帮我处理合同") is None
