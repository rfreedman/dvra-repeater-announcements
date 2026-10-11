from app.cm119 import cos_line_busy, cos_busy_from_raw, normalize_input_report


def test_normalize_input_report_strips_report_id():
    assert normalize_input_report(bytes([0, 2, 0, 0, 0])) == bytes([2, 0, 0, 0])


def test_vol_dn_active_low_idle():
    raw = bytes([0x02, 0, 0, 0])  # vol_dn bit set → active-low idle
    assert cos_line_busy(raw, line="vol_dn", active_low=True) is False


def test_vol_dn_active_low_busy():
    raw = bytes([0x00, 0, 0, 0])
    assert cos_line_busy(raw, line="vol_dn", active_low=True) is True


def test_hid_stall_treated_busy_for_vol_dn():
    assert cos_busy_from_raw(None, line="vol_dn", active_low=True) is True


def test_allstar3_busy_when_ir012_zero():
    assert cos_line_busy(bytes([0, 0, 0, 0]), line="allstar3") is True
