# -*- coding: utf-8 -*-
# Generated by the protocol buffer compiler.  DO NOT EDIT!
# NO CHECKED-IN PROTOBUF GENCODE
# source: MarketPlace.proto
# Protobuf Python Version: 6.30.2
"""Generated protocol buffer code."""
from google.protobuf import descriptor as _descriptor
from google.protobuf import descriptor_pool as _descriptor_pool
from google.protobuf import runtime_version as _runtime_version
from google.protobuf import symbol_database as _symbol_database
from google.protobuf.internal import builder as _builder
_runtime_version.ValidateProtobufRuntimeVersion(
    _runtime_version.Domain.PUBLIC,
    6,
    30,
    2,
    '',
    'MarketPlace.proto'
)
# @@protoc_insertion_point(imports)

_sym_db = _symbol_database.Default()


import _Item_pb2 as __Item__pb2
import _Character_pb2 as __Character__pb2


DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(b'\n\x11MarketPlace.proto\x12\tDC.Packet\x1a\x0b_Item.proto\x1a\x10_Character.proto\"?\n\x18SMARKETPLACE_FILTER_INFO\x12\x12\n\nfilterType\x18\x01 \x01(\x05\x12\x0f\n\x07\x66ilters\x18\x02 \x03(\t\"\x95\x01\n\x1eSC2S_MARKETPLACE_ITEM_LIST_REQ\x12\x38\n\x0b\x66ilterInfos\x18\x01 \x03(\x0b\x32#.DC.Packet.SMARKETPLACE_FILTER_INFO\x12\x10\n\x08sortType\x18\x03 \x01(\x05\x12\x12\n\nsortMethod\x18\x02 \x01(\x05\x12\x13\n\x0b\x63urrentPage\x18\x04 \x01(\x05\"\xa8\x01\n\x16SMARKETPLACE_ITEM_INFO\x12\x11\n\tlistingId\x18\x01 \x01(\x03\x12\x1e\n\x04item\x18\x02 \x01(\x0b\x32\x10.DC.Packet.SItem\x12\r\n\x05price\x18\x03 \x01(\x05\x12\x1c\n\x14remainExpirationTime\x18\x04 \x01(\x03\x12.\n\x08nickname\x18\x05 \x01(\x0b\x32\x1c.DC.Packet.SACCOUNT_NICKNAME\"|\n\x1eSS2C_MARKETPLACE_ITEM_LIST_RES\x12\x34\n\titemInfos\x18\x01 \x03(\x0b\x32!.DC.Packet.SMARKETPLACE_ITEM_INFO\x12\x13\n\x0b\x63urrentPage\x18\x02 \x01(\x05\x12\x0f\n\x07maxPage\x18\x03 \x01(\x05\"I\n!SC2S_MARKETPLACE_MY_ITEM_LIST_REQ\x12\x13\n\x0b\x63urrentPage\x18\x01 \x01(\x05\x12\x0f\n\x07perPage\x18\x02 \x01(\x05\"8\n\x17SMARKETPLACE_PRICE_INFO\x12\x0e\n\x06itemId\x18\x01 \x01(\t\x12\r\n\x05price\x18\x02 \x01(\r\"\xcf\x01\n\x19SMARKETPLACE_MY_ITEM_INFO\x12\x12\n\norderIndex\x18\x01 \x01(\x05\x12\x33\n\x08itemInfo\x18\x02 \x01(\x0b\x32!.DC.Packet.SMARKETPLACE_ITEM_INFO\x12\x1c\n\x14remainExpirationTime\x18\x03 \x01(\x03\x12\x13\n\x0bmyItemState\x18\x04 \x01(\x05\x12\x36\n\npriceInfos\x18\x05 \x03(\x0b\x32\".DC.Packet.SMARKETPLACE_PRICE_INFO\"q\n\x1eSMARKETPLACE_MY_ITEM_SLOT_INFO\x12\x12\n\norderIndex\x18\x01 \x01(\x05\x12\x10\n\x08slotType\x18\x02 \x01(\r\x12\x12\n\nslotStatus\x18\x03 \x01(\r\x12\x15\n\rexpireTimeSec\x18\x04 \x01(\r\"\xea\x01\n!SS2C_MARKETPLACE_MY_ITEM_LIST_RES\x12\x39\n\x0bmyItemInfos\x18\x01 \x03(\x0b\x32$.DC.Packet.SMARKETPLACE_MY_ITEM_INFO\x12>\n\x0bmySlotInfos\x18\x02 \x03(\x0b\x32).DC.Packet.SMARKETPLACE_MY_ITEM_SLOT_INFO\x12\x16\n\x0etotalItemCount\x18\x03 \x01(\x05\x12\x13\n\x0b\x63urrentPage\x18\x04 \x01(\x05\x12\x1d\n\x15\x61vailableOrderIndexes\x18\x05 \x03(\x05\"r\n\x1cSMARKETPLACE_TRADE_ITEM_INFO\x12\x14\n\x0citemUniqueId\x18\x01 \x01(\x04\x12\x11\n\titemCount\x18\x02 \x01(\r\x12\x19\n\x11itemContentsCount\x18\x03 \x01(\r\x12\x0e\n\x06slotId\x18\x04 \x01(\x05\"o\n\x1dSC2S_MARKETPLACE_ITEM_BUY_REQ\x12\x11\n\tlistingId\x18\x01 \x01(\x03\x12;\n\ntradeInfos\x18\x02 \x03(\x0b\x32\'.DC.Packet.SMARKETPLACE_TRADE_ITEM_INFO\"/\n\x1dSS2C_MARKETPLACE_ITEM_BUY_RES\x12\x0e\n\x06result\x18\x01 \x01(\x05\"\\\n\x1aSMARKETPLACE_REGISTER_INFO\x12\x10\n\x08uniqueId\x18\x01 \x01(\x03\x12\x11\n\titemCount\x18\x02 \x01(\r\x12\x19\n\x11itemContentsCount\x18\x03 \x01(\r\"\x99\x01\n\"SC2S_MARKETPLACE_ITEM_REGISTER_REQ\x12;\n\x0cregisterInfo\x18\x01 \x01(\x0b\x32%.DC.Packet.SMARKETPLACE_REGISTER_INFO\x12\x36\n\npriceInfos\x18\x02 \x03(\x0b\x32\".DC.Packet.SMARKETPLACE_PRICE_INFO\"4\n\"SS2C_MARKETPLACE_ITEM_REGISTER_RES\x12\x0e\n\x06result\x18\x01 \x01(\x05\"5\n SC2S_MARKETPLACE_ITEM_CANCEL_REQ\x12\x11\n\tlistingId\x18\x01 \x01(\x03\"2\n SS2C_MARKETPLACE_ITEM_CANCEL_RES\x12\x0e\n\x06result\x18\x01 \x01(\x05\"\x97\x01\n\x1cSMARKETPLACE_TRADE_SLOT_INFO\x12\x13\n\x0binventoryId\x18\x01 \x01(\r\x12\x0e\n\x06slotId\x18\x02 \x01(\r\x12\x0e\n\x06itemId\x18\x03 \x01(\t\x12\x14\n\x0citemUniqueId\x18\x04 \x01(\x04\x12\x11\n\titemCount\x18\x05 \x01(\r\x12\x19\n\x11itemContentsCount\x18\x06 \x01(\r\"t\n#SC2S_MARKETPLACE_TRANSFER_ITEMS_REQ\x12\x11\n\tlistingId\x18\x01 \x01(\x03\x12:\n\tslotInfos\x18\x02 \x03(\x0b\x32\'.DC.Packet.SMARKETPLACE_TRADE_SLOT_INFO\"5\n#SS2C_MARKETPLACE_TRANSFER_ITEMS_RES\x12\x0e\n\x06result\x18\x01 \x01(\x05\"4\n\"SS2C_MARKETPLACE_ITEM_HAS_SOLD_NOT\x12\x0e\n\x06isSold\x18\x01 \x01(\x05\"\x1c\n\x1aSC2S_MARKETPLACE_ENTER_REQ\",\n\x1aSS2C_MARKETPLACE_ENTER_RES\x12\x0e\n\x06result\x18\x01 \x01(\x05*\x91\x01\n\x12MARKETPLACE_FILTER\x12\x0f\n\x0b\x46ILTER_NONE\x10\x00\x12\x08\n\x04NAME\x10\x01\x12\n\n\x06RARITY\x10\x02\x12\x08\n\x04SLOT\x10\x03\x12\x08\n\x04TYPE\x10\x04\x12\x14\n\x10STATIC_ATTRIBUTE\x10\x05\x12\x14\n\x10RANDOM_ATTRIBUTE\x10\x06\x12\t\n\x05PRICE\x10\x07\x12\t\n\x05\x43LASS\x10\x08*L\n\x10MARKETPLACE_SORT\x12\x19\n\x15MARKETPLACE_SORT_NONE\x10\x00\x12\r\n\tASCENDING\x10\x01\x12\x0e\n\nDESCENDING\x10\x02*u\n\x19MARKETPLACE_MY_ITEM_STATE\x12\x16\n\x12MY_ITEM_STATE_NONE\x10\x00\x12\x0b\n\x07LISTING\x10\x01\x12\x13\n\x0fLISTING_EXPIRED\x10\x02\x12\x08\n\x04SOLD\x10\x03\x12\x14\n\x10TRANSFER_EXPIRED\x10\x04\x42(\n\x17\x63om.packets.marketPlaceB\x0bmarketPlaceP\x00\x62\x06proto3')

_globals = globals()
_builder.BuildMessageAndEnumDescriptors(DESCRIPTOR, _globals)
_builder.BuildTopDescriptorsAndMessages(DESCRIPTOR, 'MarketPlace_pb2', _globals)
if not _descriptor._USE_C_DESCRIPTORS:
  _globals['DESCRIPTOR']._loaded_options = None
  _globals['DESCRIPTOR']._serialized_options = b'\n\027com.packets.marketPlaceB\013marketPlaceP\000'
  _globals['_MARKETPLACE_FILTER']._serialized_start=2419
  _globals['_MARKETPLACE_FILTER']._serialized_end=2564
  _globals['_MARKETPLACE_SORT']._serialized_start=2566
  _globals['_MARKETPLACE_SORT']._serialized_end=2642
  _globals['_MARKETPLACE_MY_ITEM_STATE']._serialized_start=2644
  _globals['_MARKETPLACE_MY_ITEM_STATE']._serialized_end=2761
  _globals['_SMARKETPLACE_FILTER_INFO']._serialized_start=63
  _globals['_SMARKETPLACE_FILTER_INFO']._serialized_end=126
  _globals['_SC2S_MARKETPLACE_ITEM_LIST_REQ']._serialized_start=129
  _globals['_SC2S_MARKETPLACE_ITEM_LIST_REQ']._serialized_end=278
  _globals['_SMARKETPLACE_ITEM_INFO']._serialized_start=281
  _globals['_SMARKETPLACE_ITEM_INFO']._serialized_end=449
  _globals['_SS2C_MARKETPLACE_ITEM_LIST_RES']._serialized_start=451
  _globals['_SS2C_MARKETPLACE_ITEM_LIST_RES']._serialized_end=575
  _globals['_SC2S_MARKETPLACE_MY_ITEM_LIST_REQ']._serialized_start=577
  _globals['_SC2S_MARKETPLACE_MY_ITEM_LIST_REQ']._serialized_end=650
  _globals['_SMARKETPLACE_PRICE_INFO']._serialized_start=652
  _globals['_SMARKETPLACE_PRICE_INFO']._serialized_end=708
  _globals['_SMARKETPLACE_MY_ITEM_INFO']._serialized_start=711
  _globals['_SMARKETPLACE_MY_ITEM_INFO']._serialized_end=918
  _globals['_SMARKETPLACE_MY_ITEM_SLOT_INFO']._serialized_start=920
  _globals['_SMARKETPLACE_MY_ITEM_SLOT_INFO']._serialized_end=1033
  _globals['_SS2C_MARKETPLACE_MY_ITEM_LIST_RES']._serialized_start=1036
  _globals['_SS2C_MARKETPLACE_MY_ITEM_LIST_RES']._serialized_end=1270
  _globals['_SMARKETPLACE_TRADE_ITEM_INFO']._serialized_start=1272
  _globals['_SMARKETPLACE_TRADE_ITEM_INFO']._serialized_end=1386
  _globals['_SC2S_MARKETPLACE_ITEM_BUY_REQ']._serialized_start=1388
  _globals['_SC2S_MARKETPLACE_ITEM_BUY_REQ']._serialized_end=1499
  _globals['_SS2C_MARKETPLACE_ITEM_BUY_RES']._serialized_start=1501
  _globals['_SS2C_MARKETPLACE_ITEM_BUY_RES']._serialized_end=1548
  _globals['_SMARKETPLACE_REGISTER_INFO']._serialized_start=1550
  _globals['_SMARKETPLACE_REGISTER_INFO']._serialized_end=1642
  _globals['_SC2S_MARKETPLACE_ITEM_REGISTER_REQ']._serialized_start=1645
  _globals['_SC2S_MARKETPLACE_ITEM_REGISTER_REQ']._serialized_end=1798
  _globals['_SS2C_MARKETPLACE_ITEM_REGISTER_RES']._serialized_start=1800
  _globals['_SS2C_MARKETPLACE_ITEM_REGISTER_RES']._serialized_end=1852
  _globals['_SC2S_MARKETPLACE_ITEM_CANCEL_REQ']._serialized_start=1854
  _globals['_SC2S_MARKETPLACE_ITEM_CANCEL_REQ']._serialized_end=1907
  _globals['_SS2C_MARKETPLACE_ITEM_CANCEL_RES']._serialized_start=1909
  _globals['_SS2C_MARKETPLACE_ITEM_CANCEL_RES']._serialized_end=1959
  _globals['_SMARKETPLACE_TRADE_SLOT_INFO']._serialized_start=1962
  _globals['_SMARKETPLACE_TRADE_SLOT_INFO']._serialized_end=2113
  _globals['_SC2S_MARKETPLACE_TRANSFER_ITEMS_REQ']._serialized_start=2115
  _globals['_SC2S_MARKETPLACE_TRANSFER_ITEMS_REQ']._serialized_end=2231
  _globals['_SS2C_MARKETPLACE_TRANSFER_ITEMS_RES']._serialized_start=2233
  _globals['_SS2C_MARKETPLACE_TRANSFER_ITEMS_RES']._serialized_end=2286
  _globals['_SS2C_MARKETPLACE_ITEM_HAS_SOLD_NOT']._serialized_start=2288
  _globals['_SS2C_MARKETPLACE_ITEM_HAS_SOLD_NOT']._serialized_end=2340
  _globals['_SC2S_MARKETPLACE_ENTER_REQ']._serialized_start=2342
  _globals['_SC2S_MARKETPLACE_ENTER_REQ']._serialized_end=2370
  _globals['_SS2C_MARKETPLACE_ENTER_RES']._serialized_start=2372
  _globals['_SS2C_MARKETPLACE_ENTER_RES']._serialized_end=2416
# @@protoc_insertion_point(module_scope)
