# -*- coding: utf-8 -*-
# Generated by the protocol buffer compiler.  DO NOT EDIT!
# NO CHECKED-IN PROTOBUF GENCODE
# source: Shop.proto
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
    'Shop.proto'
)
# @@protoc_insertion_point(imports)

_sym_db = _symbol_database.Default()




DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(b'\n\nShop.proto\x12\tDC.Packet\"j\n\x14SSHOP_ITEM_BASE_INFO\x12\x10\n\x08shopType\x18\x01 \x01(\r\x12\x12\n\nshopItemId\x18\x02 \x01(\t\x12\x0e\n\x06isHave\x18\x03 \x01(\r\x12\x1c\n\x14remainDiscountTimeMS\x18\x04 \x01(\x04\"\x19\n\x17SC2S_SHOP_ITEM_LIST_REQ\"\xab\x01\n\x17SS2C_SHOP_ITEM_LIST_RES\x12\x10\n\x08loopFlag\x18\x01 \x01(\x05\x12.\n\x05infos\x18\x02 \x03(\x0b\x32\x1f.DC.Packet.SSHOP_ITEM_BASE_INFO\x12\x1b\n\x13\x63urrentTriumphLevel\x18\x03 \x01(\x05\x12\x16\n\x0enextTriumphExp\x18\x04 \x01(\x05\x12\x19\n\x11\x63urrentTriumphExp\x18\x05 \x01(\x05\"g\n\x16SC2S_SHOP_ITEM_BUY_REQ\x12\x10\n\x08shopType\x18\x01 \x01(\r\x12\x15\n\rbuyShopItemId\x18\x02 \x01(\t\x12\x15\n\rpaymentMethod\x18\x03 \x01(\r\x12\r\n\x05price\x18\x04 \x01(\r\"h\n\x16SS2C_SHOP_ITEM_BUY_RES\x12\x0e\n\x06result\x18\x01 \x01(\r\x12\x10\n\x08shopType\x18\x02 \x01(\r\x12\x15\n\rbuyShopItemId\x18\x03 \x01(\t\x12\x15\n\rpaymentMethod\x18\x04 \x01(\r\"H\n\x0cSREWARD_INFO\x12\x12\n\nrewardType\x18\x01 \x01(\t\x12\x0f\n\x07stockId\x18\x02 \x01(\t\x12\x13\n\x0brewardCount\x18\x03 \x01(\r\"O\n\x13STWITCH_REWARD_INFO\x12\x12\n\nrewardType\x18\x01 \x01(\t\x12\x0f\n\x07stockId\x18\x02 \x01(\t\x12\x13\n\x0brewardCount\x18\x03 \x01(\r\"1\n\x1dSC2S_SHOP_ENTER_GIFT_CODE_REQ\x12\x10\n\x08giftCode\x18\x01 \x01(\t\"Y\n\x1dSS2C_SHOP_ENTER_GIFT_CODE_RES\x12\x0e\n\x06result\x18\x01 \x01(\r\x12(\n\x07rewards\x18\x02 \x03(\x0b\x32\x17.DC.Packet.SREWARD_INFO\"$\n\"SC2S_SHOP_RED_STONE_SHARD_INFO_REQ\"\xb5\x01\n\x1aSSHOP_RED_STONE_SHARD_INFO\x12\x14\n\x0cproductIndex\x18\x01 \x01(\r\x12\x14\n\x0cproductPrice\x18\x02 \x01(\x02\x12\x14\n\x0cproductCount\x18\x03 \x01(\r\x12\x14\n\x0c\x64\x65signDataId\x18\x04 \x01(\t\x12\x16\n\x0eredirectionUrl\x18\x05 \x01(\t\x12\x12\n\ncustomData\x18\x06 \x01(\t\x12\x13\n\x0bproductName\x18\x07 \x01(\t\"m\n\"SS2C_SHOP_RED_STONE_SHARD_INFO_RES\x12\x34\n\x05infos\x18\x01 \x03(\x0b\x32%.DC.Packet.SSHOP_RED_STONE_SHARD_INFO\x12\x11\n\tloginType\x18\x02 \x01(\r\"L\n!SC2S_SHOP_RED_STONE_SHARD_BUY_REQ\x12\x14\n\x0cproductIndex\x18\x01 \x01(\x05\x12\x11\n\tlocalPort\x18\x02 \x01(\x05\"\x90\x01\n!SS2C_SHOP_RED_STONE_SHARD_BUY_RES\x12\x0e\n\x06result\x18\x01 \x01(\x05\x12\r\n\x05trxId\x18\x02 \x01(\x03\x12\x10\n\x08trxIdStr\x18\x03 \x01(\t\x12\x12\n\nplatformId\x18\x04 \x01(\t\x12\x13\n\x0bofferItemId\x18\x05 \x01(\t\x12\x11\n\tsandboxId\x18\x06 \x01(\t\"t\n\'SC2S_SHOP_RED_STONE_SHARD_BUY_CHECK_REQ\x12\r\n\x05\x61ppId\x18\x01 \x01(\r\x12\x0f\n\x07orderId\x18\x02 \x01(\x04\x12\x12\n\nauthorized\x18\x03 \x01(\x05\x12\x15\n\rtransactionId\x18\x04 \x01(\t\"9\n\'SS2C_SHOP_RED_STONE_SHARD_BUY_CHECK_RES\x12\x0e\n\x06result\x18\x01 \x01(\x05\"-\n\x1cSC2S_SHOP_LEGEND_UPGRADE_REQ\x12\r\n\x05param\x18\x01 \x01(\t\"X\n\x1cSS2C_SHOP_LEGEND_UPGRADE_RES\x12\x0e\n\x06result\x18\x01 \x01(\x05\x12\x13\n\x0bupgradeDate\x18\x02 \x01(\t\x12\x13\n\x0bupgradeTime\x18\x03 \x01(\t\"4\n SC2S_REFRESH_RED_STONE_SHARD_REQ\x12\x10\n\x08\x61\x63\x63\x65ssId\x18\x01 \x01(\t\"\"\n SS2C_REFRESH_RED_STONE_SHARD_RES\">\n!SC2S_SHOP_CHANGE_ACCOUNT_NAME_REQ\x12\x19\n\x11\x63hangeAccountName\x18\x01 \x01(\t\"N\n!SS2C_SHOP_CHANGE_ACCOUNT_NAME_RES\x12\x0e\n\x06result\x18\x01 \x01(\x05\x12\x19\n\x11\x63hangeAccountName\x18\x02 \x01(\t\")\n\x17SSHOP_ITEM_CHECKED_INFO\x12\x0e\n\x06shopId\x18\x01 \x03(\t\"4\n\x18SS2C_SHOP_ITEM_CHECK_NOT\x12\x18\n\x10shopNotifyStatus\x18\x01 \x01(\x05\";\n\x19SC2S_SHOP_DLC_INSTALL_REQ\x12\x0f\n\x07\x64lcType\x18\x01 \x01(\x05\x12\r\n\x05\x64lcId\x18\x02 \x01(\t\"^\n\x19SS2C_SHOP_DLC_INSTALL_RES\x12\x0e\n\x06result\x18\x01 \x01(\x05\x12\r\n\x05\x64lcId\x18\x02 \x01(\t\x12\r\n\x05\x61ppId\x18\x03 \x01(\x05\x12\x13\n\x0bredirectUrl\x18\x04 \x01(\t\"d\n!SS2C_SHOP_TWITCH_DROPS_REWARD_NOT\x12\x0e\n\x06result\x18\x01 \x01(\r\x12/\n\x07rewards\x18\x02 \x03(\x0b\x32\x1e.DC.Packet.STWITCH_REWARD_INFOB\x1a\n\x10\x63om.packets.shopB\x04shopP\x00\x62\x06proto3')

_globals = globals()
_builder.BuildMessageAndEnumDescriptors(DESCRIPTOR, _globals)
_builder.BuildTopDescriptorsAndMessages(DESCRIPTOR, 'Shop_pb2', _globals)
if not _descriptor._USE_C_DESCRIPTORS:
  _globals['DESCRIPTOR']._loaded_options = None
  _globals['DESCRIPTOR']._serialized_options = b'\n\020com.packets.shopB\004shopP\000'
  _globals['_SSHOP_ITEM_BASE_INFO']._serialized_start=25
  _globals['_SSHOP_ITEM_BASE_INFO']._serialized_end=131
  _globals['_SC2S_SHOP_ITEM_LIST_REQ']._serialized_start=133
  _globals['_SC2S_SHOP_ITEM_LIST_REQ']._serialized_end=158
  _globals['_SS2C_SHOP_ITEM_LIST_RES']._serialized_start=161
  _globals['_SS2C_SHOP_ITEM_LIST_RES']._serialized_end=332
  _globals['_SC2S_SHOP_ITEM_BUY_REQ']._serialized_start=334
  _globals['_SC2S_SHOP_ITEM_BUY_REQ']._serialized_end=437
  _globals['_SS2C_SHOP_ITEM_BUY_RES']._serialized_start=439
  _globals['_SS2C_SHOP_ITEM_BUY_RES']._serialized_end=543
  _globals['_SREWARD_INFO']._serialized_start=545
  _globals['_SREWARD_INFO']._serialized_end=617
  _globals['_STWITCH_REWARD_INFO']._serialized_start=619
  _globals['_STWITCH_REWARD_INFO']._serialized_end=698
  _globals['_SC2S_SHOP_ENTER_GIFT_CODE_REQ']._serialized_start=700
  _globals['_SC2S_SHOP_ENTER_GIFT_CODE_REQ']._serialized_end=749
  _globals['_SS2C_SHOP_ENTER_GIFT_CODE_RES']._serialized_start=751
  _globals['_SS2C_SHOP_ENTER_GIFT_CODE_RES']._serialized_end=840
  _globals['_SC2S_SHOP_RED_STONE_SHARD_INFO_REQ']._serialized_start=842
  _globals['_SC2S_SHOP_RED_STONE_SHARD_INFO_REQ']._serialized_end=878
  _globals['_SSHOP_RED_STONE_SHARD_INFO']._serialized_start=881
  _globals['_SSHOP_RED_STONE_SHARD_INFO']._serialized_end=1062
  _globals['_SS2C_SHOP_RED_STONE_SHARD_INFO_RES']._serialized_start=1064
  _globals['_SS2C_SHOP_RED_STONE_SHARD_INFO_RES']._serialized_end=1173
  _globals['_SC2S_SHOP_RED_STONE_SHARD_BUY_REQ']._serialized_start=1175
  _globals['_SC2S_SHOP_RED_STONE_SHARD_BUY_REQ']._serialized_end=1251
  _globals['_SS2C_SHOP_RED_STONE_SHARD_BUY_RES']._serialized_start=1254
  _globals['_SS2C_SHOP_RED_STONE_SHARD_BUY_RES']._serialized_end=1398
  _globals['_SC2S_SHOP_RED_STONE_SHARD_BUY_CHECK_REQ']._serialized_start=1400
  _globals['_SC2S_SHOP_RED_STONE_SHARD_BUY_CHECK_REQ']._serialized_end=1516
  _globals['_SS2C_SHOP_RED_STONE_SHARD_BUY_CHECK_RES']._serialized_start=1518
  _globals['_SS2C_SHOP_RED_STONE_SHARD_BUY_CHECK_RES']._serialized_end=1575
  _globals['_SC2S_SHOP_LEGEND_UPGRADE_REQ']._serialized_start=1577
  _globals['_SC2S_SHOP_LEGEND_UPGRADE_REQ']._serialized_end=1622
  _globals['_SS2C_SHOP_LEGEND_UPGRADE_RES']._serialized_start=1624
  _globals['_SS2C_SHOP_LEGEND_UPGRADE_RES']._serialized_end=1712
  _globals['_SC2S_REFRESH_RED_STONE_SHARD_REQ']._serialized_start=1714
  _globals['_SC2S_REFRESH_RED_STONE_SHARD_REQ']._serialized_end=1766
  _globals['_SS2C_REFRESH_RED_STONE_SHARD_RES']._serialized_start=1768
  _globals['_SS2C_REFRESH_RED_STONE_SHARD_RES']._serialized_end=1802
  _globals['_SC2S_SHOP_CHANGE_ACCOUNT_NAME_REQ']._serialized_start=1804
  _globals['_SC2S_SHOP_CHANGE_ACCOUNT_NAME_REQ']._serialized_end=1866
  _globals['_SS2C_SHOP_CHANGE_ACCOUNT_NAME_RES']._serialized_start=1868
  _globals['_SS2C_SHOP_CHANGE_ACCOUNT_NAME_RES']._serialized_end=1946
  _globals['_SSHOP_ITEM_CHECKED_INFO']._serialized_start=1948
  _globals['_SSHOP_ITEM_CHECKED_INFO']._serialized_end=1989
  _globals['_SS2C_SHOP_ITEM_CHECK_NOT']._serialized_start=1991
  _globals['_SS2C_SHOP_ITEM_CHECK_NOT']._serialized_end=2043
  _globals['_SC2S_SHOP_DLC_INSTALL_REQ']._serialized_start=2045
  _globals['_SC2S_SHOP_DLC_INSTALL_REQ']._serialized_end=2104
  _globals['_SS2C_SHOP_DLC_INSTALL_RES']._serialized_start=2106
  _globals['_SS2C_SHOP_DLC_INSTALL_RES']._serialized_end=2200
  _globals['_SS2C_SHOP_TWITCH_DROPS_REWARD_NOT']._serialized_start=2202
  _globals['_SS2C_SHOP_TWITCH_DROPS_REWARD_NOT']._serialized_end=2302
# @@protoc_insertion_point(module_scope)
