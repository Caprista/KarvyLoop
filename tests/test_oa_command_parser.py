from remote_oa_stage.oa_command_parser import OACommand, parse_oa_command


def test_batch_approval_and_range_are_normalized():
    assert parse_oa_command("2、5、8-10批准") == OACommand("approve", (2, 5, 8, 9, 10))


def test_reject_captures_reason():
    assert parse_oa_command("拒绝2，原因：材料不完整") == OACommand(
        "reject", (2,), "材料不完整", ""
    )


def test_forward_captures_target():
    assert parse_oa_command("转发3给张三") == OACommand("forward", (3,), "", "张三")


def test_continue_refresh_retry_commands():
    assert parse_oa_command("？") == OACommand("continue")
    assert parse_oa_command("刷新列表") == OACommand("refresh")
    assert parse_oa_command("重试") == OACommand("retry")


def test_ambiguous_request_is_not_guessed():
    assert parse_oa_command("帮我处理合同") is None
