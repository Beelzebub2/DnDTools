// Node.js version of capture.py using 'cap' and protobufjs
const Cap = require('cap').Cap;
const decoders = require('cap').decoders;
const PROTOCOL = require('cap').PROTOCOL;
const os = require('os');
const fs = require('fs-extra');
const path = require('path');
const protobuf = require('protobufjs');

class PacketProtocol {
  static S2C_LOBBY_CHARACTER_INFO_RES = 44;
  static S2C_ACCOUNT_CHARACTER_LIST_RES = 18;
  static isValidType(type) {
    return [
      PacketProtocol.S2C_LOBBY_CHARACTER_INFO_RES,
      PacketProtocol.S2C_ACCOUNT_CHARACTER_LIST_RES
    ].includes(type);
  }
}

class PacketCapture {
  constructor(interfaceName = 'Ethernet', portRange = [20200, 20300]) {
    this.interfaceName = interfaceName;
    this.portRange = portRange;
    this.packetData = Buffer.alloc(0);
    this.MAX_BUFFER_SIZE = 1024 * 1024;
    this.expectedPacketLength = null;
    this.expectedProtoType = null;
    this.dataDir = path.resolve(__dirname, '../data');
    fs.ensureDirSync(this.dataDir);
  }

  getLocalIP() {
    const ifaces = os.networkInterfaces();
    const addrs = ifaces[this.interfaceName] || [];
    for (const addr of addrs) {
      if (addr.family === 'IPv4' && !addr.internal) {
        return addr.address;
      }
    }
    return null;
  }

  validatePacketHeader(length, protoType, padding) {
    return (
      length >= 100 && length <= 2 * 1024 * 1024 &&
      PacketProtocol.isValidType(protoType) &&
      [0, 256].includes(padding)
    );
  }

  async verifyPacket() {
    if (this.packetData.length !== this.expectedPacketLength) return false;
    try {
      const body = this.packetData.slice(8);
      const root = await protobuf.load(
        path.resolve(__dirname, '../networking/protos/Lobby.proto')
      );
      const Msg = root.lookupType('SS2C_LOBBY_CHARACTER_INFO_RES');
      Msg.decode(body);
      return true;
    } catch (err) {
      console.error('Packet verification failed:', err);
      return false;
    }
  }

  async savePacketData() {
    const body = this.packetData.slice(8);
    const root = await protobuf.load(
      path.resolve(__dirname, '../networking/protos/Lobby.proto')
    );
    const Msg = root.lookupType('SS2C_LOBBY_CHARACTER_INFO_RES');
    const message = Msg.decode(body);
    const json = Msg.toObject(message, { enums: String, longs: String, defaults: true });

    const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
    const dataFile = path.join(this.dataDir, `${timestamp}.json`);
    const rootFile = path.resolve(__dirname, '../packet_data.json');

    await fs.writeJson(dataFile, json, { spaces: 2 });
    await fs.writeJson(rootFile, json, { spaces: 2 });
    console.log(`Saved packet data to ${dataFile} and ${rootFile}`);
  }

  async processPacket(dataBuf) {
    this.packetData = Buffer.concat([this.packetData, dataBuf]);
    if (this.packetData.length > this.MAX_BUFFER_SIZE) {
      console.warn('Buffer exceeded max size');
      this.resetState();
      return;
    }

    if (this.expectedPacketLength === null && this.packetData.length >= 8) {
      const length = this.packetData.readUInt32LE(0);
      const protoType = this.packetData.readUInt16LE(4);
      const padding = this.packetData.readUInt16LE(6);

      if (!this.validatePacketHeader(length, protoType, padding)) {
        console.warn(`Invalid header: len=${length}, type=${protoType}, pad=${padding}`);
        this.resetState();
        return;
      }
      console.log(`New packet header: len=${length}, type=${protoType}, pad=${padding}`);
      this.expectedPacketLength = length;
      this.expectedProtoType = protoType;
    }

    if (this.expectedPacketLength && this.expectedProtoType) {
      if (this.packetData.length > this.expectedPacketLength) {
        this.packetData = this.packetData.slice(0, this.expectedPacketLength);
      }

      if (this.packetData.length === this.expectedPacketLength) {
        if (await this.verifyPacket()) {
          await this.savePacketData();
        }
        this.resetState();
      }
    }
  }

  resetState() {
    this.packetData = Buffer.alloc(0);
    this.expectedPacketLength = null;
    this.expectedProtoType = null;
  }

  capture() {
    const localIP = this.getLocalIP();
    if (!localIP) {
      console.error(`Unable to find IP for interface ${this.interfaceName}`);
      return;
    }

    const cap = new Cap();
    const filter = `ip dst ${localIP} and tcp src portrange ${this.portRange[0]}-${this.portRange[1]}`;
    const bufSize = 10 * 1024 * 1024;
    const buffer = Buffer.alloc(65535);
    const linkType = cap.open(this.interfaceName, filter, bufSize, buffer);

    cap.setMinBytes && cap.setMinBytes(0);

    cap.on('packet', async (nbytes, trunc) => {
      const ret = decoders.Ethernet(buffer);
      if (ret.info.type === PROTOCOL.ETHERNET.IPV4) {
        const ret2 = decoders.IPV4(buffer, ret.offset);
        if (ret2.info.protocol === PROTOCOL.IP.TCP) {
          const ret3 = decoders.TCP(buffer, ret2.offset);
          if (ret3.info.payloadLength > 0) {
            const payload = buffer.slice(ret3.offset, ret3.offset + ret3.info.payloadLength);
            await this.processPacket(payload);
          }
        }
      }
    });

    console.log('Listening on', this.interfaceName, 'with filter', filter);
  }
}

// Run capture
const pc = new PacketCapture();
pc.capture();