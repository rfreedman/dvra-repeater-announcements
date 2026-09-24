# RA-64M Dual-Repeater Parallel Announcement System - Hardware and Software

This documentation covers the hardware modifications and custom Python software control script required to interface a single **Masters Communications RA-64M** (C-Media CM119A chipset) with two separate repeaters. 

The configuration achieves the following operational parameters:
* **Shared Output:** Transmits identical announcement audio to both repeaters simultaneously.
* **Synchronized PTT:** Keys both transmitters at the exact same moment.
* **Independent Logic Checks:** Reads both COS lines separately, allowing the software to automatically defer the announcement if **either** repeater channel is currently busy.
* **Zero Audio Input:** Keeps the incoming RX audio lines un-jumpered since incoming audio monitoring is handled at the repeater controllers or skipped entirely.

---

## 1. Hardware Modification Guide (Under PCB)

To mirror the output and transmission lines across both physical Mini-DIN-6 jacks while keeping the busy logic inputs independent, solder small-gauge jump wires (e.g., 30 AWG wire-wrap wire) on the underside of the RA-64M PCB between these target pins:

### Pin Jumper Connections
* **PTT Bridging:** Solder a jumper wire from **Pin 3 (PTT) of the TX/RX jack** to **Pin 3 (PTT) of the RX jack**. This ties the hardware keying circuit together so that when the software triggers the PTT bit, both repeaters transmit simultaneously.
* **TX Audio Splitting:** Solder a jumper wire from **Pin 5 (TX Audio) of the TX/RX jack** to **Pin 5 (TX Audio) of the RX jack**. This feeds the audio output stream into both systems.
  * *Note on Line Balance:* If one repeater requires a lower input drive level than the other, insert an inline **10k Ω trimmer potentiometer** into the jumper wire path heading to the second jack to scale down its level independently.

### Kept Separate (Do NOT Jumper)
* **COS Lines (Pin 6):** Do not join Pin 6 of the two jacks. Leaving them isolated allows the individual hardware registers on the CM119A chip to read their busy states separately:
  * **Repeater 1 COS (TX/RX Jack Pin 6)** maps natively to the chip's **VOL_DN** line.
  * **Repeater 2 COS (RX Jack Pin 6)** maps natively to the chip's **VOL_UP** line.

---

## 2. Linux Environment Configuration

To permit the custom Python loop to communicate directly with the raw USB HID interface nodes instead of letting the OS kernel lock it down exclusively as a system sound card device, create a custom rules node.

Create file `/etc/udev/rules.d/99-ra64m.rules`:
```text
# Masters Communications RA-64M (CM119A chipset custom access)
SUBSYSTEM=="usb", ATTRS{idVendor}=="0d8c", ATTRS{idProduct}=="000e", MODE="0666", GROUP="plugdev"
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0d8c", ATTRS{idProduct}=="000e", MODE="0666", GROUP="plugdev"
```

Reload the system rules via bash:
```bash
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Install the required lightweight python module to parse the raw USB HID reports:
```bash
pip install hid
```

---

## 3. Python Implementation Script

This non-blocking execution script reads the separate busy inputs via the CM119A HID input byte arrays and gates the announcement execution.

```python
import hid
import time
import subprocess

# C-Media CM119A Vendor and Product IDs
VENDOR_ID = 0x0D8C
PRODUCT_ID = 0x000E

def get_ra64m_device():
    try:
        device = hid.device()
        device.open(VENDOR_ID, PRODUCT_ID)
        device.set_nonblocking(True)
        return device
    except IOError as e:
        print(f"Error opening RA-64M device: {e}")
        return None

def set_ptt(device, state: bool):
    """
    Controls GPIO4 to actuate the synchronous PTT circuit on the board.
    Report ID 0x05 handles the core CM119A GPIO controls.
    """
    # Byte layout: [Report ID, GPIO_Values, GPIO_Direction, Mute_Values]
    gpio_val = 0x10 if state else 0x00  # Bit 4 corresponds to GPIO4
    gpio_dir = 0x10                     # Pin configured for Output mode
    
    buf = [0x05, gpio_val, gpio_dir, 0x00]
    device.send_feature_report(buf)

def check_repeaters_busy(device, active_high=False):
    """
    Evaluates the current state of both isolated physical COS inputs.
    Returns a tuple of booleans: (rptr1_busy, rptr2_busy)
    """
    try:
        data = device.read(64)
        if not data:
            return False, False # Base state: No transmission toggles detected
        
        # Byte 1 maps the raw hardware button/logic toggles
        button_byte = data[0]
        
        # Bit 0 isolates VOL_UP (Repeater 2), Bit 1 isolates VOL_DN (Repeater 1)
        rptr2_raw = bool(button_byte & 0x01)
        rptr1_raw = bool(button_byte & 0x02)
        
        # Adjust logic states depending on if your specific repeaters push 
        # a positive voltage voltage when busy (Active High) or pull to Ground (Active Low).
        if active_high:
            return rptr1_raw, rptr2_raw
        else:
            return not rptr1_raw, not rptr2_raw
            
    except IOError:
        return False, False

def play_announcement(audio_file_path):
    """
    Pushes the raw WAV audio straight to the target system audio card.
    """
    # Adjust standard output card flags (-D plughw:X,Y) if system defaults vary
    subprocess.run(["aplay", "-q", audio_file_path])

def main():
    dev = get_ra64m_device()
    if not dev:
        return

    announcement_queued = True  # Setup flag for demonstration
    audio_file = "/path/to/announcement.wav"

    print("RA-64M Parallel Announcement Engine running...")

    try:
        while True:
            if announcement_queued:
                # Set active_high=True if repeaters provide high voltage when busy
                rptr1_busy, rptr2_busy = check_repeaters_busy(dev, active_high=False)
                
                if rptr1_busy or rptr2_busy:
                    print(f"Announcement Deferred: [Rptr1 Busy: {rptr1_busy}] [Rptr2 Busy: {rptr2_busy}]")
                    time.sleep(1.0)  # Check interval while channel is occupied
                else:
                    print("Channels clear. Triggering parallel key down.")
                    set_ptt(dev, True)
                    time.sleep(0.1)  # Pre-transmission delay for receiver squelch tail opening
                    
                    play_announcement(audio_file)
                    
                    set_ptt(dev, False)
                    announcement_queued = False  # Dequeue task
            
            time.sleep(0.1)  # Quiet interval loop
            
    except KeyboardInterrupt:
        set_ptt(dev, False)
        dev.close()
        print("\nShutdown clean.")

if __name__ == "__main__":
    main()
```
