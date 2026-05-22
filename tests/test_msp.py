"""Tests du protocole MSP v1 et v2, et des parseurs de commandes."""

import struct
import pytest
from unittest.mock import MagicMock
from betaflight.msp import MSPProtocol, MSPCommand, _crc8_dvb_s2
from betaflight.msp_codes import MSPCodes
from betaflight.commands import BetaflightCommands, _DataReader


# ── Helpers ───────────────────────────────────────────────────────────

def make_protocol():
    conn = MagicMock()
    conn.is_connected = True
    return MSPProtocol(conn), conn


def make_commands():
    proto, conn = make_protocol()
    return BetaflightCommands(proto), proto, conn


def v1_frame(cmd: int, payload: bytes = b'') -> bytes:
    """Construit une trame MSP v1 réponse ($M>)."""
    size     = len(payload)
    checksum = size ^ cmd
    for b in payload:
        checksum ^= b
    return b'$M>' + bytes([size, cmd]) + payload + bytes([checksum])


def v2_frame(cmd: int, payload: bytes = b'', flag: int = 0) -> bytes:
    """Construit une trame MSP v2 réponse ($X>)."""
    header   = bytes([flag]) + struct.pack("<HH", cmd, len(payload))
    crc      = _crc8_dvb_s2(header + payload)
    return b'$X>' + header + payload + bytes([crc])


def set_conn_response(conn, frame: bytes):
    """Simule la réception d'une trame MSP v1 en 3 lectures."""
    size = frame[3]
    conn.read.side_effect = [frame[:3], frame[3:5], frame[5:]]


def set_conn_response_v2(conn, frame: bytes):
    """Simule la réception d'une trame MSP v2 en 3 lectures."""
    size = struct.unpack_from("<H", frame, 6)[0]
    # 3 bytes preamble+dir | 5 bytes header | payload+crc
    conn.read.side_effect = [frame[:3], frame[3:8], frame[8:]]


# ── Tests MSP v1 : encodage ───────────────────────────────────────────

def test_build_frame_checksum_no_payload():
    proto, _ = make_protocol()
    frame = proto._build_frame(MSPCommand.MSP_STATUS)
    assert frame[:3] == b'$M<'
    assert frame[3] == 0      # size
    assert frame[4] == 101    # cmd
    assert frame[5] == 101    # checksum = 0 ^ 101

def test_build_frame_with_payload():
    proto, _ = make_protocol()
    payload = bytes([42, 43, 44])
    frame   = proto._build_frame(MSPCommand.MSP_SET_PID, payload)
    assert frame[:3] == b'$M<'
    assert frame[3] == 3      # size
    assert frame[4] == 202    # MSP_SET_PID
    expected_cs = 3 ^ 202 ^ 42 ^ 43 ^ 44
    assert frame[-1] == expected_cs

def test_build_frame_is_v1_for_cmd_lt_256():
    proto, _ = make_protocol()
    frame = proto._build_frame(100)
    assert frame[:2] == b'$M'

# ── Tests MSP v1 : décodage ───────────────────────────────────────────

def test_read_response_v1_valid():
    proto, conn = make_protocol()
    payload  = b'\x01\x02\x03'
    frame    = v1_frame(MSPCommand.MSP_RAW_IMU, payload)
    set_conn_response(conn, frame)
    result = proto.read_response()
    assert result is not None
    assert result["cmd"]     == MSPCommand.MSP_RAW_IMU
    assert result["payload"] == payload
    assert result["version"] == 1

def test_read_response_v1_bad_checksum():
    proto, conn = make_protocol()
    frame = bytearray(v1_frame(MSPCommand.MSP_STATUS, b'\x01'))
    frame[-1] ^= 0xFF
    set_conn_response(conn, bytes(frame))
    assert proto.read_response() is None

def test_read_response_v1_bad_preamble():
    proto, conn = make_protocol()
    conn.read.side_effect = [b'$Z>', b'\x00\x65', b'\x65']
    assert proto.read_response() is None

def test_read_response_v1_bad_direction():
    proto, conn = make_protocol()
    conn.read.side_effect = [b'$M<']
    assert proto.read_response() is None

# ── Tests MSP v2 : encodage ───────────────────────────────────────────

def test_crc8_dvb_s2_known_value():
    # CRC8/DVB-S2 de b'\x00' = 0
    assert _crc8_dvb_s2(b'\x00') == 0
    # Vecteur de test : CRC de [0x00, 0x00, 0x00, 0x00, 0x00] = 0
    assert _crc8_dvb_s2(bytes(5)) == 0

def test_build_frame_v2_structure():
    proto, _ = make_protocol()
    frame = proto._build_frame_v2(0x3000, b'\xAB\xCD')
    assert frame[:2] == b'$X'
    assert frame[2]  == ord('<')
    cmd  = struct.unpack_from("<H", frame, 4)[0]
    size = struct.unpack_from("<H", frame, 6)[0]
    assert cmd  == 0x3000
    assert size == 2
    assert frame[8]  == 0xAB
    assert frame[9]  == 0xCD
    # Vérifier le CRC
    header  = frame[3:8]
    payload = frame[8:-1]
    assert frame[-1] == _crc8_dvb_s2(header + payload)

def test_send_command_uses_v2_for_large_cmd():
    proto, conn = make_protocol()
    proto.send_command(0x3000, b'')
    written = conn.write.call_args[0][0]
    assert written[:2] == b'$X'

def test_send_command_uses_v1_for_small_cmd():
    proto, conn = make_protocol()
    proto.send_command(101, b'')
    written = conn.write.call_args[0][0]
    assert written[:2] == b'$M'

# ── Tests MSP v2 : décodage ───────────────────────────────────────────

def test_read_response_v2_valid():
    proto, conn = make_protocol()
    payload = b'\x11\x22\x33'
    frame   = v2_frame(0x3006, payload)
    set_conn_response_v2(conn, frame)
    result = proto.read_response()
    assert result is not None
    assert result["cmd"]     == 0x3006
    assert result["payload"] == payload
    assert result["version"] == 2

def test_read_response_v2_bad_crc():
    proto, conn = make_protocol()
    frame = bytearray(v2_frame(0x3006, b'\x01'))
    frame[-1] ^= 0xFF
    set_conn_response_v2(conn, bytes(frame))
    assert proto.read_response() is None

# ── Tests MSPCodes ────────────────────────────────────────────────────

def test_msp_codes_values():
    assert MSPCodes.MSP_STATUS    == 101
    assert MSPCodes.MSP_RAW_IMU   == 102
    assert MSPCodes.MSP_ANALOG    == 110
    assert MSPCodes.MSP_PID       == 112
    assert MSPCodes.MSP_SET_PID   == 202
    assert MSPCodes.MSP_EEPROM_WRITE == 250
    assert MSPCodes.MSP_SET_REBOOT   == 68
    assert MSPCodes.MSP2_GET_TEXT    == 0x3006

def test_msp_command_alias():
    assert MSPCommand is MSPCodes
    assert MSPCommand.MSP_STATUS == 101

# ── Tests DataReader ──────────────────────────────────────────────────

def test_data_reader_basic():
    d = _DataReader(bytes([1, 0xAB, 0xCD, 0x00, 0x00, 0x01, 0x00]))
    assert d.read_u8()  == 1
    assert d.read_u16() == 0xCDAB
    assert d.read_u32() == 0x00010000

def test_data_reader_signed():
    d = _DataReader(struct.pack("<h", -500) + struct.pack("<i", -100000))
    assert d.read_s16() == -500
    assert d.read_s32() == -100000

def test_data_reader_returns_zero_on_overflow():
    d = _DataReader(b'\x01')
    d.read_u8()
    assert d.read_u8()  == 0
    assert d.read_u16() == 0
    assert d.remaining == 0

def test_data_reader_read_text():
    text    = "BTFL"
    payload = bytes([len(text)]) + text.encode()
    d       = _DataReader(payload)
    assert d.read_text() == "BTFL"

def test_data_reader_skip():
    d = _DataReader(bytes([1, 2, 3, 4]))
    d.skip(2)
    assert d.read_u8() == 3

# ── Tests parseurs de commandes ───────────────────────────────────────

def test_get_imu_data():
    bf, proto, conn = make_commands()
    # 9 × int16 : accel(3) + gyro(3) + mag(3)
    raw     = struct.pack("<9h", 2048, 0, -2048, 164, 0, -164, 100, 200, 300)
    frame   = v1_frame(MSPCodes.MSP_RAW_IMU, raw)
    set_conn_response(conn, frame)
    result = bf.get_imu_data()
    assert result is not None
    assert result["accel"]["x"] == pytest.approx(1.0, abs=0.001)     # 2048/2048
    assert result["accel"]["z"] == pytest.approx(-1.0, abs=0.001)    # -2048/2048
    assert result["gyro"]["x"]  == pytest.approx(40.0, abs=0.1)      # 164*4/16.4
    assert result["mag"]["x"]   == 100

def test_get_attitude():
    bf, proto, conn = make_commands()
    raw   = struct.pack("<3h", 150, -90, 45)
    frame = v1_frame(MSPCodes.MSP_ATTITUDE, raw)
    set_conn_response(conn, frame)
    result = bf.get_attitude()
    assert result["roll_deg"]  == pytest.approx(15.0)
    assert result["pitch_deg"] == pytest.approx(-9.0)
    assert result["yaw_deg"]   == 45

def test_get_altitude():
    bf, proto, conn = make_commands()
    raw   = struct.pack("<i", 15000) + struct.pack("<h", 50)  # 150 m, 50 cm/s
    frame = v1_frame(MSPCodes.MSP_ALTITUDE, raw)
    set_conn_response(conn, frame)
    result = bf.get_altitude()
    assert result["altitude_m"]     == pytest.approx(150.0)
    assert result["variometer_cms"] == 50

def test_get_battery():
    bf, proto, conn = make_commands()
    # byte0=168 (16.8V legacy), mah=250, rssi=800, amperage=1500 (15A), voltage=1680 (16.80V)
    raw   = struct.pack("<BHHhH", 168, 250, 800, 1500, 1680)
    frame = v1_frame(MSPCodes.MSP_ANALOG, raw)
    set_conn_response(conn, frame)
    result = bf.get_battery()
    assert result["voltage_v"]  == pytest.approx(16.80)
    assert result["amperage_a"] == pytest.approx(15.0)
    assert result["mah_drawn"]  == 250
    assert result["rssi"]       == 800

def test_get_pid_values():
    bf, proto, conn = make_commands()
    # 3 axes × 3 bytes = 9 bytes
    raw   = bytes([42, 40, 30,  45, 43, 32,  50, 45, 0,  0,0,0,  0,0,0,  0,0,0,  0,0,0,  50,50,0,  0,0,0,  0,0,0])
    frame = v1_frame(MSPCodes.MSP_PID, raw[:30])
    set_conn_response(conn, frame)
    result = bf.get_pid_values()
    assert result["roll"]  == {"p": 42, "i": 40, "d": 30}
    assert result["pitch"] == {"p": 45, "i": 43, "d": 32}
    assert result["yaw"]   == {"p": 50, "i": 45, "d": 0}

def test_get_modes():
    bf, proto, conn = make_commands()
    raw   = struct.pack("<2BHH", 0, 0, 1700, 2100)   # 1 mode
    frame = v1_frame(MSPCodes.MSP_MODE_RANGES, raw)
    set_conn_response(conn, frame)
    result = bf.get_modes()
    assert len(result) == 1
    assert result[0]["min"] == 1700
    assert result[0]["max"] == 2100

def test_get_rc():
    bf, proto, conn = make_commands()
    channels = [1500, 1500, 1000, 1500, 1000, 1000, 1000, 1000]
    raw   = struct.pack("<8H", *channels)
    frame = v1_frame(MSPCodes.MSP_RC, raw)
    set_conn_response(conn, frame)
    result = bf.get_rc()
    assert result["count"]      == 8
    assert result["channels"][0] == 1500
    assert result["channels"][2] == 1000

def test_get_feature_config():
    bf, proto, conn = make_commands()
    # AIRMODE (bit 21) + MOTOR_STOP (bit 4) = 0x200010
    mask  = (1 << 21) | (1 << 4)
    raw   = struct.pack("<I", mask)
    frame = v1_frame(MSPCodes.MSP_FEATURE_CONFIG, raw)
    set_conn_response(conn, frame)
    result = bf.get_feature_config()
    assert "AIRMODE"    in result["enabled"]
    assert "MOTOR_STOP" in result["enabled"]
    assert result["mask"] == mask

def test_get_api_version():
    bf, proto, conn = make_commands()
    raw   = bytes([2, 1, 46])  # protocol=2, major=1, minor=46
    frame = v1_frame(MSPCodes.MSP_API_VERSION, raw)
    set_conn_response(conn, frame)
    result = bf.get_api_version()
    assert result["api_version"] == "1.46.0"
    assert bf.api_version        == (1, 46)

def test_get_battery_state():
    bf, proto, conn = make_commands()
    # cells=4, capacity=1500, voltage_legacy=168, mah=300, amp=2000, state=0, voltage=1680
    raw   = struct.pack("<BBHBHHBH", 4, 0xDC, 0x05, 168, 300, 2000, 0, 1680)
    # Rebuild properly:
    raw = bytes([4]) + struct.pack("<H", 1500) + bytes([168]) + struct.pack("<HHB", 300, 2000, 0) + struct.pack("<H", 1680)
    frame = v1_frame(MSPCodes.MSP_BATTERY_STATE, raw)
    set_conn_response(conn, frame)
    result = bf.get_battery_state()
    assert result["cell_count"]   == 4
    assert result["capacity_mah"] == 1500
    assert result["voltage_v"]    == pytest.approx(16.80)
    assert result["amperage_a"]   == pytest.approx(20.0)
    assert result["battery_state"] == "OK"

def test_get_voltage_meters():
    bf, proto, conn = make_commands()
    raw   = bytes([1, 168, 2, 126])  # id=1, 16.8V ; id=2, 12.6V
    frame = v1_frame(MSPCodes.MSP_VOLTAGE_METERS, raw)
    set_conn_response(conn, frame)
    result = bf.get_voltage_meters()
    assert len(result)       == 2
    assert result[0]["id"]         == 1
    assert result[0]["voltage_v"]  == pytest.approx(16.8)
    assert result[1]["voltage_v"]  == pytest.approx(12.6)

def test_get_fc_variant():
    bf, proto, conn = make_commands()
    raw   = b'BTFL'
    frame = v1_frame(MSPCodes.MSP_FC_VARIANT, raw)
    set_conn_response(conn, frame)
    result = bf.get_fc_variant()
    assert result["identifier"] == "BTFL"

def test_get_fc_version():
    bf, proto, conn = make_commands()
    raw   = bytes([4, 4, 0])
    frame = v1_frame(MSPCodes.MSP_FC_VERSION, raw)
    set_conn_response(conn, frame)
    result = bf.get_fc_version()
    assert result["version"] == "4.4.0"

def test_set_pid_values():
    bf, proto, conn = make_commands()
    # get_pid_values retourne des valeurs actuelles
    pid_payload = bytes([40, 38, 28,  42, 40, 30,  50, 45, 0] + [0] * 21)
    frame       = v1_frame(MSPCodes.MSP_PID, pid_payload)
    conn.read.side_effect = [frame[:3], frame[3:5], frame[5:]]

    bf.set_pid_values({"roll": {"p": 45, "i": 42, "d": 32}})

    # write appelé 2 fois : 1 pour GET_PID, 1 pour SET_PID
    assert conn.write.call_count == 2
    # La 2e trame est MSP_SET_PID avec le payload modifié
    written = conn.write.call_args_list[1][0][0]
    assert written[:3] == b'$M<'
    assert written[4]  == MSPCodes.MSP_SET_PID
    assert written[5]  == 45   # roll P (modifié)
    assert written[6]  == 42   # roll I (modifié)
    assert written[7]  == 32   # roll D (modifié)
    assert written[8]  == 42   # pitch P (inchangé)
    assert written[9]  == 40   # pitch I (inchangé)
    assert written[10] == 30   # pitch D (inchangé)
