// ef_psram: Efabless EF_PSRAM_CTRL, a controller for SPI / QSPI / QPI serial PSRAM and SRAM, with an
// AHB-Lite slave interface. Source: https://github.com/efabless/EF_PSRAM_CTRL @ 87dbffb
// (hdl/rtl/EF_PSRAM_CTRL.v, hdl/rtl/bus_wrapper/EF_PSRAM_CTRL_AHBL.v). Changes for rtl-env: the two files are
// combined into one with a single timescale; logic unchanged. Licensed under Apache-2.0 (see LICENSE).
`timescale 1ns / 1ps

/*
	Copyright 2024 Efabless Corp.

	Author: Efabless Corp. (ip_admin@efabless.com)

	Licensed under the Apache License, Version 2.0 (the "License");
	you may not use this file except in compliance with the License.
	You may obtain a copy of the License at

	    http://www.apache.org/licenses/LICENSE-2.0

	Unless required by applicable law or agreed to in writing, software
	distributed under the License is distributed on an "AS IS" BASIS,
	WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
	See the License for the specific language governing permissions and
	limitations under the License.

*/
/*
    QSPI PSRAM Controller 

    Pseudostatic RAM (PSRAM) is DRAM combined with a self-refresh circuit. 
    It appears externally as slower SRAM, albeit with a density/cost advantage 
    over true SRAM, and without the access complexity of DRAM.

    The controller was designed after:
        - https://www.issi.com/WW/pdf/66-67WVS4M8ALL-BLL.pdf and
        - https://www.microchip.com/en-us/parametric-search/514
    utilizing SPI, QSPI and QPI modes
*/




module EF_PSRAM_CTRL (
    input   wire            clk,
    input   wire            rst_n,
    input   wire [23:0]     addr,
    input   wire [31: 0]    data_i,
    output  wire [31: 0]    data_o,
    input   wire [2:0]      size,
    input   wire            start,
    output  wire            done,
    input   wire  [3:0]     wait_states,
    input   wire  [7:0]     cmd,
    input   wire            rd_wr,
    input   wire            qspi,
    input   wire            qpi,
    input   wire            short_cmd,
    
    output  reg             sck,
    output  reg             ce_n,
    input   wire [3:0]      din,
    output  wire [3:0]      dout,
    output  wire [3:0]      douten
);
    localparam  IDLE = 1'b0, 
                BUSY = 1'b1;

    reg         state, nstate;
    reg [7:0]   counter;
    reg [23:0]  saddr;
    reg [7:0]   data [3:0]; 

    //wire[7:0]   CMD_38H = 8'h38;

    always @*
        case (state)
            IDLE: if(start) nstate = BUSY; else nstate = IDLE;
            BUSY: if(done) nstate = IDLE; else nstate = BUSY;
        endcase 

    always @ (posedge clk or negedge rst_n)
        if(!rst_n) state <= IDLE;
        else state <= nstate;

    // Drive the Serial Clock (sck) @ clk/2 
    always @ (posedge clk or negedge rst_n)
        if(!rst_n) 
            sck <= 1'b0;
        else if(done)//(state == IDLE) 
            sck <= 1'b0;
        else if(~ce_n) 
            sck <= ~ sck;
        

    // ce_n logic
    always @ (posedge clk or negedge rst_n)
        if(!rst_n) 
            ce_n <= 1'b1;
        else if(done)
            ce_n <= 1'b1;
        else if (state == BUSY) 
            ce_n <= 1'b0;
        else 
            ce_n <= 1'b1;

    // The transaction counter
    wire [7:0]  wait_start  = (~qpi ? 8 : 2)                    // The command 
                            + ((qpi | qspi) ? 6 : 24);          // The Address        
    wire [7:0]  data_start  = wait_start + (rd_wr ? wait_states : 0);
    wire [7:0]  data_count  = ((qpi | qspi) ? 2 : 8) * size;    
    wire [7:0]  final_count = short_cmd ? 8 : data_start + data_count;
    
    assign done     = (counter == final_count);

    always @ (posedge clk or negedge rst_n)
        if(!rst_n) 
            counter <= 8'b0;
        else if(sck & ~done) 
            counter <= counter + 1'b1;
        else if(state == IDLE) 
            counter <= 8'b0;

    // Data Out
    wire [3:0]      dout_spi;
    wire [3:0]      dout_qspi;
    wire [3:0]      dout_qpi;
    
    wire [7:0]      spi_bit_index = (counter < 40)  ?   39 - (counter)  :
                                    (counter < 48)  ?   55 - (counter)  :
                                    (counter < 56)  ?   71 - (counter)  :
                                    (counter < 64)  ?   87 - (counter)  :
                                    0;

    assign dout_spi =   (counter < 8)   ?   cmd[7 - counter]            :
                        (counter < 32)  ?   addr[31 - counter]          : 
                        data_i[spi_bit_index];
                        
                        
    assign dout_qspi=   (counter < 8)   ?   cmd[7 - counter]            :
                        (counter == 8)  ?   addr[23:20]                 : 
                        (counter == 9)  ?   addr[19:16]                 :
                        (counter == 10) ?   addr[15:12]                 :
                        (counter == 11) ?   addr[11:8]                  :
                        (counter == 12) ?   addr[7:4]                   :
                        (counter == 13) ?   addr[3:0]                   :
                        (counter == 14) ?   data_i[7:4]                 :
                        (counter == 15) ?   data_i[3:0]                 :
                        (counter == 16) ?   data_i[15:12]               :
                        (counter == 17) ?   data_i[11:8]                :
                        (counter == 18) ?   data_i[23:20]               :
                        (counter == 19) ?   data_i[19:16]               :
                        (counter == 20) ?   data_i[31:28]               :                   
                        (counter == 21) ?   data_i[27:24]                 :
                        4'b0000;

    assign dout_qpi =   (counter == 0)  ?   cmd[7:4]                    :
                        (counter == 1)  ?   cmd[3:0]                    :
                        (counter == 2)  ?   addr[23:20]                 : 
                        (counter == 3)  ?   addr[19:16]                 :
                        (counter == 4)  ?   addr[15:12]                 :
                        (counter == 5)  ?   addr[11:8]                  :
                        (counter == 6)  ?   addr[7:4]                   :
                        (counter == 7)  ?   addr[3:0]                   :
                        (counter == 8)  ?   data_i[7:4]                 :
                        (counter == 9)  ?   data_i[3:0]                 :
                        (counter == 10) ?   data_i[15:12]               :
                        (counter == 11) ?   data_i[11:8]                :
                        (counter == 12) ?   data_i[23:20]               :
                        (counter == 13) ?   data_i[19:16]               :
                        (counter == 14) ?   data_i[31:28]               :                   
                        (counter == 15) ?   data_i[27:24]                 :
                        4'b0000;
    
    assign  dout    =   qpi     ?   dout_qpi    :
                        qspi    ?   dout_qspi   :
                        dout_spi;
         
    // Data In
    wire [7:0]  byte_index = (counter - data_start) >> ((qpi | qspi) ? 1 : 3);
    always @ (posedge clk)
        if(counter >= data_start && counter <= final_count)
            if(sck) 
                if(qpi | qspi) 
                    data[byte_index] <= {data[byte_index][3:0], din}; 
                else
                    data[byte_index] <= {data[byte_index][6:0], din[1]};  
    assign data_o = {data[3], data[2], data[1], data[0]};

    // Out Enable
    wire [3:0]  douten_qpi;
    wire [3:0]  douten_qspi;
    wire [3:0]  douten_spi;
    wire        has_wait_states = (wait_states != 0) & rd_wr;
    assign douten_spi   =   4'b0001;
    assign douten_qspi  =   (counter < 8)                               ?   4'b0001 :
                            (counter < 14)                              ?   4'b1111 :
                            ((counter < data_start) & has_wait_states)  ?   4'b0000 :
                            (rd_wr)                                     ?   4'b0000 :
                            4'b1111;
    assign douten_qpi   =   (counter < 2)                               ?   4'b1111 :
                            (counter < 8)                               ?   4'b1111 :
                            ((counter < data_start) & has_wait_states)  ?   4'b0000 :
                            (rd_wr)                                     ?   4'b0000 :
                            4'b1111;
                            
    assign  douten      =   qpi     ?   douten_qpi    :
                            qspi    ?   douten_qspi   :
                            douten_spi;

endmodule

/*
	Copyright 2024 Efabless Corp.

	Author: Efabless Corp. (ip_admin@efabless.com)

	Licensed under the Apache License, Version 2.0 (the "License");
	you may not use this file except in compliance with the License.
	You may obtain a copy of the License at

	    http://www.apache.org/licenses/LICENSE-2.0

	Unless required by applicable law or agreed to in writing, software
	distributed under the License is distributed on an "AS IS" BASIS,
	WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
	See the License for the specific language governing permissions and
	limitations under the License.

*/


/*
        Address Map:
        0x0070_0000 - 0x0000_0000   : Read/Write from the external PSRAM
        0x008x_01xx                 : RD Command Register
        0x008x_02xx                 : WR Command Register
        0x008x_04xx                 : Enter QPI Command Register
        0x008x_08xx                 : Exit QPI Command Register
        0x008x_10xx                 : Wait States Register
        0x008x_20xx                 : Mode Register, {qpi, qspi}
        0x008x_40xx                 : Initiate/Stop Enter QPI Mode
        0x008x_80xx                 : Initiate/Stop Exit QPI Mode
*/

// Using EBH Command
module EF_PSRAM_CTRL_AHBL #(parameter REGISTER_HWDATA = 1) 
(
    // AHB-Lite Slave Interface
    input   wire            HCLK,
    input   wire            HRESETn,
    input   wire            HSEL,
    input   wire [31:0]     HADDR,
    input   wire [31:0]     HWDATA,
    input   wire [1:0]      HTRANS,
    input   wire [2:0]      HSIZE,
    input   wire            HWRITE,
    input   wire            HREADY,
    output  reg             HREADYOUT,
    output  reg [31:0]      HRDATA,

    // External Interface to Quad I/O
    output  wire            sck,
    output  wire            ce_n,
    input   wire [3:0]      din,
    output  wire [3:0]      dout,
    output  wire [3:0]      douten     
);

    localparam  ST_IDLE = 1'b0,
                ST_WAIT = 1'b1;

    reg         state, nstate;

    //AHB-Lite Address Phase Regs
    reg         last_HSEL;
    reg [31:0]  last_HADDR;
    reg         last_HWRITE;
    reg [1:0]   last_HTRANS;
    reg [2:0]   last_HSIZE;

    wire [2:0]  size =  (last_HSIZE == 0) ? 1 :
                        (last_HSIZE == 1) ? 2 :
                        (last_HSIZE == 2) ? 4 : 4;

    // wire        data_cfg        =   ~HADDR[23];
    reg data_cfg;
    wire        last_data_cfg   =   ~last_HADDR[23];
    wire        control         =   HADDR[22];
    wire        ahb_addr_phase  =   HTRANS[1] & HSEL & HREADY;

    wire        rd_wr;
    reg         rd_wr_reg;
    wire [3:0]  wait_states;
    wire [7:0]  cmd;
    wire        qspi;
    wire        qpi;
    
    // Hand-shaking Signals
    wire        start;
    wire        done;
    reg [31:0]  data_i;
    reg [31:0]  data_i_sized;
    wire [31:0]  data_o;
    
    // handle hsize 
    wire is_byte     = (last_HSIZE == 3'b000);
    wire is_half     = (last_HSIZE == 3'b001);
    wire is_word     = (last_HSIZE == 3'b010);

    wire byte_0      = is_byte & (last_HADDR[1:0] == 2'b00);
    wire byte_1      = is_byte & (last_HADDR[1:0] == 2'b01);
    wire byte_2      = is_byte & (last_HADDR[1:0] == 2'b10);
    wire byte_3      = is_byte & (last_HADDR[1:0] == 2'b11);

    wire half_0      = is_half & ~last_HADDR[1];
    wire half_2      = is_half & last_HADDR[1];
    
    always @(*) begin
        if (is_word) begin
            data_i_sized = data_i;  // Assign the full word
        end
        else if (is_half) begin
            if (half_0) begin
                data_i_sized = data_i[15:0];  // Assign lower half-word
            end
            else begin
                data_i_sized = data_i[31:16];  // Assign upper half-word
            end
        end
        else begin
            if (byte_0) begin
                data_i_sized = data_i[7:0];  // Assign first byte
            end
            else if (byte_1) begin
                data_i_sized = data_i[15:8];  // Assign second byte
            end
            else if (byte_2) begin
                data_i_sized = data_i[23:16];  // Assign third byte
            end
            else begin
                data_i_sized = data_i[31:24];  // Assign fourth byte
            end
        end
    end

    always @(*) begin
        if (is_word) begin
            HRDATA = data_o;  // Assign the full word
        end
        else if (is_half) begin
            if (half_0) begin
                HRDATA = data_o[15:0];  // Assign lower half-word
            end
            else begin
                HRDATA = {data_o[15:0], 16'b0};  // Assign upper half-word
            end
        end else begin
            if (byte_0) begin
                HRDATA = data_o[7:0];  // Assign first byte
            end
            else if (byte_1) begin
                HRDATA = {data_o[7:0], 8'b0};  // Assign second byte
            end
            else if (byte_2) begin
                HRDATA = {data_o[7:0], 16'b0};  // Assign third byte
            end
            else begin
                HRDATA = {data_o[7:0], 24'b0};  // Assign fourth byte
            end
        end
    end

    always@ (posedge HCLK or negedge HRESETn) begin
        if (!HRESETn) begin
            last_HSEL       <= 0;
            last_HADDR      <= 0;
            last_HWRITE     <= 0;
            last_HTRANS     <= 0;
            // last_HSIZE      <= 0;
        end
        else if(HREADY) begin
            last_HSEL       <= HSEL;
            last_HADDR      <= HADDR;
            last_HWRITE     <= HWRITE;
            last_HTRANS     <= HTRANS;
            // last_HSIZE      <= HSIZE;
        end
    end

    always@(posedge HCLK or negedge HRESETn) begin 
        if(!HRESETn) 
            last_HSIZE <= 0;
        else if (HTRANS[1] & HREADYOUT)
                last_HSIZE<=HSIZE;
    end

    // Configuration Interface
    reg [7:0]   RD_CMD_REG;
    reg [7:0]   WR_CMD_REG;
    reg [7:0]   EQPI_CMD_REG;
    reg [7:0]   XQPI_CMD_REG;
    reg [3:0]   WAIT_STATES_REG;
    reg [1:0]   MODE_REG;
    reg         ENTER_QPI_REG;
    reg         EXIT_QPI_REG;

    always@(posedge HCLK or negedge HRESETn)
        if(!HRESETn) begin
            RD_CMD_REG      <= 8'h03;
            WR_CMD_REG      <= 8'h02;
            EQPI_CMD_REG    <= 8'h35;
            XQPI_CMD_REG    <= 8'hF5;
            WAIT_STATES_REG <= 8'd0;
            MODE_REG        <= 2'd0;
            ENTER_QPI_REG   <= 1'b0;
            EXIT_QPI_REG    <= 1'b0;
        end
        else if(~last_data_cfg & last_HWRITE & last_HSEL & last_HTRANS[1]) begin
            if(last_HADDR[8])   RD_CMD_REG      <= HWDATA;
            if(last_HADDR[9])   WR_CMD_REG      <= HWDATA;
            if(last_HADDR[10])  EQPI_CMD_REG    <= HWDATA;
            if(last_HADDR[11])  XQPI_CMD_REG    <= HWDATA;
            if(last_HADDR[12])  WAIT_STATES_REG <= HWDATA;
            if(last_HADDR[13])  MODE_REG        <= HWDATA;
            if(last_HADDR[14])  ENTER_QPI_REG   <= HWDATA;
            if(last_HADDR[15])  EXIT_QPI_REG    <= HWDATA;
            //if(last_HADDR[16])  ENTER_QPI_REG <= 1'b0;
            //if(last_HADDR[16])  EXIT_QPI_REG <= 1'b0;
        end

    assign  wait_states = WAIT_STATES_REG;
    assign  qspi        = MODE_REG[0];
    assign  qpi         = MODE_REG[1];

    // PSRAM Interface
    always @ (posedge HCLK or negedge HRESETn)
        if(HRESETn == 0) 
            state <= ST_IDLE;
        else 
            state <= nstate;

    always @* begin
        case(state)
            ST_IDLE :   
                if(start) 
                    nstate = ST_WAIT;
                else
                    nstate = ST_IDLE;

            ST_WAIT :   
                if((done & last_data_cfg))
                    nstate = ST_IDLE;
                else
                    nstate = ST_WAIT; 
        endcase
    end
    reg         last_ahb_addr_phase;

    // HREADYOUT Generation
    always @(posedge HCLK or negedge HRESETn)
        if(!HRESETn) 
            HREADYOUT <= 1'b1;
        else
            case (state)
                ST_IDLE :   
                    if((last_ahb_addr_phase|ahb_addr_phase)) 
                        HREADYOUT <= 1'b0;
                    else 
                        HREADYOUT <= 1'b1;

                ST_WAIT :   
                    if((done & last_data_cfg))  
                        HREADYOUT <= 1'b1;
                    else 
                        HREADYOUT <= 1'b0;
            endcase

    assign  rd_wr   =   ~last_HWRITE;
    always @(posedge HCLK or negedge HRESETn)
        if(!HRESETn)
            rd_wr_reg <= 1'b0;
        else if (last_HTRANS[1])
            rd_wr_reg <= rd_wr;
    assign  cmd     =   ENTER_QPI_REG   ?   EQPI_CMD_REG    :
                        EXIT_QPI_REG    ?   XQPI_CMD_REG    :
                        rd_wr_reg           ?   RD_CMD_REG      :
                        WR_CMD_REG; 
    wire    short_cmd   =   (ENTER_QPI_REG|EXIT_QPI_REG);
    wire [23:0] mctrl_addr = {1'b0,last_HADDR[22:0]};

    //generate    
    //    if(REGISTER_HWDATA) begin
        

            always @(posedge HCLK or negedge HRESETn) begin
                if(!HRESETn)
                    last_ahb_addr_phase <= 0;
                else
                    last_ahb_addr_phase <= ahb_addr_phase;
            end
            
            always @(posedge HCLK)
                if(last_ahb_addr_phase)
                    data_i <= HWDATA;
            
            assign  start   =   ( last_ahb_addr_phase & (state == ST_IDLE ) & data_cfg );
        
        always@(posedge HCLK or negedge HRESETn)
        if(!HRESETn)
            data_cfg <= 0;
        else if (ahb_addr_phase)
            data_cfg <= ~HADDR[23];

        //end 
        //else begin
        /*
            wire [31:0] data_i = HWDATA;
            assign  start   =   ( ahb_addr_phase & (state == ST_IDLE ) & data_cfg );
        */
        //end    
    //endgenerate
    
    EF_PSRAM_CTRL MCTRL(
        .clk(HCLK), 
        .rst_n(HRESETn), 
	    .addr(mctrl_addr), 
        .data_i(data_i_sized),
        .data_o(data_o),
        .size(size),
        .start(start),
        .done(done), 
        .wait_states(wait_states),
        .cmd(cmd),
        .rd_wr(rd_wr_reg),
        .qspi(qspi),
        .qpi(qpi),
        .sck(sck), 
        .ce_n(ce_n), 
        .din(din), 
        .dout(dout), 
        .douten(douten),
        .short_cmd(short_cmd)
    );
    
endmodule
