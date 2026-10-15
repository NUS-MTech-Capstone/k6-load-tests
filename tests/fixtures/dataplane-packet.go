package protocol

import (
	"encoding/binary"
	"errors"
)

type Type byte

const (
	Input            Type = 0x00
	Output           Type = 0x01
	Error            Type = 0x02
	SessionEvent     Type = 0x07
	Resize           Type = 0x10
	Heartbeat        Type = 0x11
	ClientClose      Type = 0x12
	ClientEndSession Type = 0x18
)

type Packet struct {
	Type Type
	Data []byte
}

func Parse(data []byte) (Packet, error) {
	if len(data) == 0 {
		return Packet{}, errors.New("empty terminal packet")
	}
	return Packet{Type: Type(data[0]), Data: append([]byte(nil), data[1:]...)}, nil
}

func Encode(packet Packet) []byte {
	return append([]byte{byte(packet.Type)}, packet.Data...)
}

func ParseResize(data []byte) (uint16, uint16, error) {
	if len(data) != 4 {
		return 0, 0, errors.New("resize packet must contain four bytes")
	}
	cols := binary.BigEndian.Uint16(data[0:2])
	rows := binary.BigEndian.Uint16(data[2:4])
	if cols == 0 || rows == 0 {
		return 0, 0, errors.New("terminal dimensions must be positive")
	}
	return cols, rows, nil
}
