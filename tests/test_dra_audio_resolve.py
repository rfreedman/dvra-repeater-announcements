from __future__ import annotations

from app.dra_playback import auto_select_dra_outputs, resolve_dra_audio_indices


def _dev(name: str, *, out: int = 2, inp: int = 0) -> dict:
    return {
        "name": name,
        "max_output_channels": out,
        "max_input_channels": inp,
    }


def test_auto_select_prefers_alsa_hw_over_pipewire_duplicate():
    devices = [
        (4, _dev("USB PnP Sound Device: Audio (hw:1,0)")),
        (16, _dev("alsa_output.usb-C-Media_Electronics_Inc._USB_PnP_Sound_Device-00.analog-stereo")),
    ]
    selected = auto_select_dra_outputs(devices)
    assert [i for i, _ in selected] == [4]


def test_auto_select_keeps_two_hw_for_two_dras():
    devices = [
        (4, _dev("USB PnP Sound Device: Audio (hw:1,0)")),
        (6, _dev("USB PnP Sound Device: Audio (hw:2,0)")),
        (16, _dev("alsa_output.usb-C-Media_Electronics_Inc._USB_PnP_Sound_Device-00.analog-stereo")),
        (17, _dev("alsa_output.usb-C-Media_Electronics_Inc._USB_PnP_Sound_Device-00.analog-stereo")),
    ]
    assert [i for i, _ in auto_select_dra_outputs(devices)] == [4, 6]


def test_auto_select_mac_fallback_when_no_hw_names():
    devices = [(2, _dev("USB PnP Sound Device"))]
    assert auto_select_dra_outputs(devices) == devices


def test_resolve_uses_auto_select_when_specs_omitted(monkeypatch):
    fake = [
        (4, _dev("USB PnP Sound Device: Audio (hw:1,0)")),
        (16, _dev("alsa_output.usb-C-Media_Electronics_Inc._USB_PnP_Sound_Device-00.analog-stereo")),
    ]
    monkeypatch.setattr("app.dra_playback.list_dra_output_devices", lambda: fake)
    assert resolve_dra_audio_indices(device_specs=None) == [4]


def test_resolve_honors_explicit_specs(monkeypatch):
    fake = [
        (4, _dev("USB PnP Sound Device: Audio (hw:1,0)")),
        (16, _dev("alsa_output.usb-C-Media_Electronics_Inc._USB_PnP_Sound_Device-00.analog-stereo")),
    ]
    monkeypatch.setattr("app.dra_playback.list_dra_output_devices", lambda: fake)
    assert resolve_dra_audio_indices(device_specs=["alsa_output.usb"]) == [16]
