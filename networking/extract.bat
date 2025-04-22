protodump -file "C:\Program Files\IRONMACE\Dark and Darker\DungeonCrawler\Binaries\Win64\DungeonCrawler.exe" -output protos
pip install protobuf
pip install --upgrade protobuf
protoc.exe --proto_path=protos --python_out=protos protos/*.proto
pause