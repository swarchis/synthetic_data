FROM ubuntu:24.04
RUN apt-get update && apt-get install -y iverilog verilator yosys python3-pip git
RUN pip install cocotb pytest --break-system-packages
WORKDIR /work
