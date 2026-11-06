// ef_i2s: Efabless EF_I2S (I2S microphone receiver with FIFO and voice-activity detection), APB wrapper.
// Sources: https://github.com/efabless/EF_I2S @ d335705 (hdl/rtl/EF_I2S.v, hdl/rtl/bus_wrappers/EF_I2S_APB.v)
// and https://github.com/efabless/EF_IP_UTIL @ a2b3fb6 (ef_util_fifo, ef_util_gating_cell from hdl/ef_util_lib.v).
// Changes for rtl-env: the files are combined into one, timescale/default_nettype lines are replaced by
// a single timescale. Logic is unchanged. Licensed under the Apache License 2.0 (see LICENSE).
`timescale 1ns / 1ps

// ---- from EF_IP_UTIL (Apache-2.0) ----
module ef_util_fifo #(parameter DW=8, AW=4)(
    input   wire            clk,
    input   wire            rst_n,
    input   wire            rd,
    input   wire            wr,
    input   wire            flush,
    input   wire [DW-1:0]   wdata,
    output  wire            empty,
    output  wire            full,
    output  wire [DW-1:0]   rdata,
    output  wire [AW-1:0]   level    
);

    localparam  DEPTH = 2**AW;

    //Internal Signal declarations
    reg [DW-1:0]  array_reg [DEPTH-1:0];
    reg [AW-1:0]  w_ptr_reg;
    reg [AW-1:0]  w_ptr_next;
    reg [AW-1:0]  w_ptr_succ;
    reg [AW-1:0]  r_ptr_reg;
    reg [AW-1:0]  r_ptr_next;
    reg [AW-1:0]  r_ptr_succ;

    // Level
    reg [AW-1:0] level_reg;
    reg [AW-1:0] level_next;      
    reg full_reg;
    reg empty_reg;
    reg full_next;
    reg empty_next;

    wire w_en;

    always @ (posedge clk)
        if(w_en) begin
            array_reg[w_ptr_reg] <= wdata;
        end

    assign rdata = array_reg[r_ptr_reg];   
    assign w_en = wr & ~full_reg;           

    //State Machine
    always @ (posedge clk, negedge rst_n) begin 
        if(!rst_n)
            begin
                w_ptr_reg <= 'b0;
                r_ptr_reg <= 'b0;
                full_reg  <= 1'b0;
                empty_reg <= 1'b1;
                level_reg <= 'd0;
            end
        else if(flush)
            begin
                w_ptr_reg <= 'b0;
                r_ptr_reg <= 'b0;
                full_reg  <= 1'b0;
                empty_reg <= 1'b1;
                level_reg <= 'd0;
            end
        else
            begin
                w_ptr_reg <= w_ptr_next;
                r_ptr_reg <= r_ptr_next;
                full_reg  <= full_next;
                empty_reg <= empty_next;
                level_reg <= level_next;
            end
    end

    //Next State Logic
    always @* begin
        w_ptr_succ  =   w_ptr_reg + 1;
        r_ptr_succ  =   r_ptr_reg + 1;
        w_ptr_next  =   w_ptr_reg;
        r_ptr_next  =   r_ptr_reg;
        full_next   =   full_reg;
        empty_next  =   empty_reg;
        level_next  =   level_reg;

        case({w_en,rd})
            //2'b00: nop
            2'b01: 
                if(~empty_reg) begin
                    r_ptr_next = r_ptr_succ;
                    full_next = 1'b0;
                    level_next = level_reg - 1;
                    if (r_ptr_succ == w_ptr_reg)
                        empty_next = 1'b1;
                end
            
            2'b10: 
                if(~full_reg) begin
                    w_ptr_next = w_ptr_succ;
                    empty_next = 1'b0;
                    level_next = level_reg + 1;
                    if (w_ptr_succ == r_ptr_reg)
                        full_next = 1'b1;
                end
            
            2'b11: begin
                w_ptr_next = w_ptr_succ;
                r_ptr_next = r_ptr_succ;
            end
        endcase
    end

    //Set Full and Empty
    assign full = full_reg;
    assign empty = empty_reg;
    assign level = level_reg;
  
endmodule

module ef_util_gating_cell(
    `ifdef USE_POWER_PINS 
    input   wire    vpwr,
    input   wire    vgnd,
    `endif // USE_POWER_PINS
    input   wire    clk,
    input   wire    rst_n,
    input   wire    clk_en,
    output  wire    clk_o
);
    `ifdef CLKG_SKY130_HD
    (* keep *) sky130_fd_sc_hd__dlclkp_4 clk_gate(
    `ifdef USE_POWER_PINS 
        .VPWR(vpwr), 
        .VGND(vgnd), 
        .VNB(vpwr),
		.VPB(vgnd),
    `endif // CLKG_SKY130_HD
        .GCLK(clk_o), 
        .GATE(clk_en), 
        .CLK(clk)
        );
    `elsif CLKG_GENERIC
    assign clk_o = clk & clk_en; 
    `else
    assign clk_o = clk;
    `endif // CLKG_SKY130_HD
endmodule

// ---- from EF_I2S (Apache-2.0) ----
/*
	Copyright 2023 Efabless Corp.

	Author: Mohamed Shalan (mshalan@efabless.com)
	
	Licensed under the Apache License, Version 2.0 (the "License"); 
	you may not use this file except in compliance with the License. 
	You may obtain a copy of the License at:
	http://www.apache.org/licenses/LICENSE-2.0
	Unless required by applicable law or agreed to in writing, software 
	distributed under the License is distributed on an "AS IS" BASIS, 
	WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. 
	See the License for the specific language governing permissions and 
	limitations under the License.
*/







module i2s_rx (
    input   wire        clk,
    input   wire        rst_n,
    input   wire        sd,
    input   wire        ws,
    input   wire        sck,

    input   wire        left_justified,
    output  reg         rdy,
    output  reg  [31:0] sample
);

    reg [31:0] sr;

    reg ws_dly0, ws_dly;

    reg last_ws, ws_npulse, ws_ppulse; always @(posedge clk, negedge rst_n)  if(!rst_n)begin last_ws <= 1'b1; ws_npulse <= 1'b0; ws_ppulse <= 1'b0; end else begin last_ws <= ws; ws_npulse <= ~ws & last_ws; ws_ppulse <= ws & ~last_ws; end
    reg last_sck; wire sck_pulse; always @(posedge clk, negedge rst_n) if(!rst_n) last_sck <= 1'b0; else last_sck <= sck; assign sck_pulse = sck & ~last_sck;

    reg last_nsck; wire sck_npulse; always @(posedge clk, negedge rst_n) if(!rst_n) last_nsck <= 1'b0; else last_nsck <= sck; assign sck_npulse = ~sck & last_nsck;
    reg last_ws_dly, ws_dly_npulse, ws_dly_ppulse; always @(posedge clk, negedge rst_n)  if(!rst_n)begin last_ws_dly <= 1'b0; ws_dly_npulse <= 1'b0; ws_dly_ppulse <= 1'b0; end else begin last_ws_dly <= ws_dly; ws_dly_npulse <= ~ws_dly & last_ws_dly; ws_dly_ppulse <= ws_dly & ~last_ws_dly; end
    
    always @(posedge clk or negedge rst_n)
        if(!rst_n) begin
            ws_dly <= 0;
            ws_dly0 <= 0;
        end
        else if(sck_npulse) begin
            ws_dly0 <= ws;
            ws_dly <= ws_dly0;
        end

    wire ws_pulse = ws_ppulse | ws_npulse;
    wire ws_dly_pulse = ws_dly_ppulse | ws_dly_npulse;
    
    always @(posedge clk or negedge rst_n)
        if(!rst_n)
            sr <= 32'b0;
        else
            if(sck_pulse) 
                sr <= {sr[30:0], sd};

    always @(posedge clk or negedge rst_n)
        if(!rst_n)
            sample <= 32'b0;
        else
            if(left_justified & ws_pulse)
                sample <= sr;
            else if(~left_justified & ws_dly_pulse)
                sample <= sr;
    
    // adding a flag to prevent the initial ready assertion 
    reg first; 
    always @(posedge clk or negedge rst_n)
        if(!rst_n)
          first <= 1'b0;
        else if (ws_pulse | ws_dly_pulse)
          first <= 1'b1;

    always @(posedge clk or negedge rst_n)
        if(!rst_n)
            rdy <= 1'b0;
        else if(left_justified)
            rdy <= ws_pulse & first;
        else
            rdy <= ws_dly_pulse & first;

endmodule


module EF_I2S #(parameter DW=32, AW=4) (
    input   wire            clk,
    input   wire            rst_n,

    output  wire            ws,
    output  wire            sck,
    input   wire            sdi,
    //output  wire        sdo,

    input   wire            fifo_en,
    input   wire            fifo_rd,
    input   wire            fifo_clr,
    input   wire [AW-1:0]   fifo_level_threshold,
    input   wire            fifo_flush,
    output  wire            fifo_full,
    output  wire            fifo_empty,
    output  wire [AW-1:0]   fifo_level,
    output  wire            fifo_level_above,
    output  wire [31:0]     fifo_rdata,

    input   wire            sign_extend,
    input   wire            left_justified,
    input   wire [5:0]      sample_size,
    input   wire [7:0]      sck_prescaler,
    input   wire [31:0]     avg_threshold,
    output  wire            avg_flag,
    input   wire            avg_en,
    input   wire            avg_sel,        // 0: 32 samples, 1: 64 samples
    input   wire [31:0]     zcr_threshold,
    output  wire            zcr_flag,
    input   wire            zcr_en,
    input   wire            zcr_sel,        // 0: 32 samples, 1: 64 samples
    
    output  wire            vad_flag,
    
    input   wire [1:0]      channels,       // 10: left, 01: right, 11: both (stereo)
    input   wire            en         
); 

    reg         sck_reg;
    reg         ws_reg;
    reg [7:0]   prescaler;
    reg [4:0]   bit_ctr;

    wire        sample_rdy;

    assign      sck = sck_reg;
    assign      ws = ws_reg;

    //wire [31:0] rsample, lsample;

    wire [31:0]  sample;

    // The prescaler
    always @ (posedge clk, negedge rst_n)
        if(!rst_n)
            prescaler <= 8'b0;
        else if(en)
            if(prescaler == 8'b0)
                prescaler <= sck_prescaler;
            else 
                prescaler <= prescaler - 1'b1;

    // The Serial Clock (SCK)
    always @ (posedge clk, negedge rst_n)
        if(!rst_n)
            sck_reg <= 1'b0;
        else if(en==1'b1 && prescaler == 8'b0)
            sck_reg <= !sck_reg;
    
    // The Bit Counter
    always @ (posedge clk, negedge rst_n)
        if(!rst_n)
            bit_ctr <= 5'b0;
        else if(en == 1'b1 && prescaler == 8'b0 && sck_reg == 1'b1)
            bit_ctr <= bit_ctr + 1'b1;
    
    // Word Select Line Register
    always @ (posedge clk, negedge rst_n)
        if(!rst_n)
            ws_reg <= 1'b1;
        else
            if(en==1'b1 && bit_ctr==5'b0 && prescaler==8'b0 && sck_reg==1'b1)
                ws_reg <= !ws_reg;

    wire [1:0]  current_channel = 1 << (left_justified == ~ws);
    wire        fifo_wr = fifo_en & sample_rdy & |(current_channel & channels);
    wire [31:0] sample_sign = sign_extend ? {32{sample[31]}} << sample_size : 32'b0;
    wire [31:0] fifo_wdata = (sample >> (32-sample_size)) | sample_sign;
    
    assign      fifo_level_above = (fifo_level > fifo_level_threshold) | fifo_full;

    // Averaging Logic
    reg  [31:0] sum;
    reg  [5:0]  sum_ctr;
    wire [31:0] abs_sample_value = (fifo_wdata[31]) ? ~fifo_wdata : fifo_wdata;
    always @ (posedge clk, negedge rst_n)
        if(!rst_n)
            sum_ctr <= 'b0;
        else
            if(sample_rdy & |(current_channel & channels))
                sum_ctr <= sum_ctr + 1'b1;
    
    wire sum_ctr_zero = avg_sel ? (sum_ctr == 6'b0) : (sum_ctr[4:0] == 5'b0);

    always @ (posedge clk, negedge rst_n)
        if(!rst_n)
            sum <= 'b0;
        else if(sample_rdy & |(current_channel & channels))
            if(sum_ctr_zero)
                sum <= abs_sample_value;
            else
               if(avg_en) sum <= sum + abs_sample_value;

    wire avg_gt_threshold = avg_sel ? (sum[31:6] > avg_threshold) : (sum[31:5] > avg_threshold);
    assign avg_flag = avg_en & (avg_gt_threshold);

    // ZCR Logic
    reg prev_sign;
    reg  [31:0] zcr;
    reg  [5:0]  zc_ctr;
    always @ (posedge clk, negedge rst_n)
        if(!rst_n)
            zc_ctr <= 'b0;
        else
            if(sample_rdy & |(current_channel & channels))
                zc_ctr <= zc_ctr + 1'b1;
    
    always @(posedge clk, negedge rst_n)
        if(!rst_n)
            prev_sign <= 1'b0;
        else if(sample_rdy & |(current_channel & channels))
            prev_sign <= fifo_wdata[31];

    wire zc_ctr_zero = zcr_sel ? (zc_ctr == 6'b0) : (zc_ctr[4:0] == 5'b0);

    always @ (posedge clk, negedge rst_n)
        if(!rst_n)
            zcr <= 'b0;
        else if(sample_rdy & |(current_channel & channels))
            if(zc_ctr_zero)
                zcr <= 0;
            else
                if(zcr_en) zcr <= zcr + (prev_sign ^ fifo_wdata[31]);
    
    wire zcr_gt_threshold = zcr > zcr_threshold;
    assign zcr_flag = zcr_en & (zcr_gt_threshold);

    assign vad_flag = avg_flag & zcr_flag;

    i2s_rx RX (
        .clk(clk),
        .rst_n(rst_n),
        .sd(sdi),
        .ws(ws),
        .sck(sck),
        .left_justified(left_justified),
        .sample(sample),
        .rdy(sample_rdy)
    );

    ef_util_fifo #(.DW(DW), .AW(AW)) I2SFIFO (
        .clk(clk),
        .rst_n(rst_n),
        // .clr(fifo_clr),
        .flush(fifo_flush),
        .rd(fifo_rd),
        .wr(fifo_wr),
        .wdata(fifo_wdata),
        .empty(fifo_empty),
        .full(fifo_full),
        .rdata(fifo_rdata),
        .level(fifo_level)    
    );

endmodule

/*
	Copyright 2025 Efabless Corp.

	Author: Efabless Corp. (ip_admin@efabless.com)

	Licensed under the Apache License, Version 2.0 (the "License");
	you may not use this file except in compliance with the License.
	You may obtain a copy of the License at

	    www.apache.org/licenses/LICENSE-2.0

	Unless required by applicable law or agreed to in writing, software
	distributed under the License is distributed on an "AS IS" BASIS,
	WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
	See the License for the specific language governing permissions and
	limitations under the License.

*/

/* THIS FILE IS GENERATED, DO NOT EDIT */


module EF_I2S_APB #(
    parameter DW = 32,
    AW = 4
) (

    input  wire         PCLK,
    input  wire         PRESETn,
    input  wire         PWRITE,
    input  wire [ 31:0] PWDATA,
    input  wire [ 31:0] PADDR,
    input  wire         PENABLE,
    input  wire         PSEL,
    output wire         PREADY,
    output wire [ 31:0] PRDATA,
    output wire         IRQ,
    output wire [1-1:0] ws,
    output wire [1-1:0] sck,
    input  wire [1-1:0] sdi
);

  localparam RXDATA_REG_OFFSET = 16'h0000;
  localparam PR_REG_OFFSET = 16'h0004;
  localparam AVGT_REG_OFFSET = 16'h0008;
  localparam ZCRT_REG_OFFSET = 16'h000C;
  localparam CTRL_REG_OFFSET = 16'h0010;
  localparam CFG_REG_OFFSET = 16'h0014;
  localparam RX_FIFO_LEVEL_REG_OFFSET = 16'hFE00;
  localparam RX_FIFO_THRESHOLD_REG_OFFSET = 16'hFE04;
  localparam RX_FIFO_FLUSH_REG_OFFSET = 16'hFE08;
  localparam IM_REG_OFFSET = 16'hFF00;
  localparam MIS_REG_OFFSET = 16'hFF04;
  localparam RIS_REG_OFFSET = 16'hFF08;
  localparam IC_REG_OFFSET = 16'hFF0C;

  reg [0:0] GCLK_REG;
  wire clk_g;

  wire clk_gated_en = GCLK_REG[0];
  ef_util_gating_cell clk_gate_cell (

      // USE_POWER_PINS
      .clk(PCLK),
      .clk_en(clk_gated_en),
      .clk_o(clk_g)
  );

  wire          clk = clk_g;
  wire          rst_n = PRESETn;

  wire          apb_valid = PSEL & PENABLE;
  wire          apb_we = PWRITE & apb_valid;
  wire          apb_re = ~PWRITE & apb_valid;

  wire [ 1-1:0] fifo_en;
  wire [ 1-1:0] fifo_rd;
  wire [AW-1:0] fifo_level_threshold;
  wire [ 1-1:0] fifo_flush;
  wire [ 1-1:0] fifo_full;
  wire [ 1-1:0] fifo_empty;
  wire [AW-1:0] fifo_level;
  wire [ 1-1:0] fifo_level_above;
  wire [32-1:0] fifo_rdata;
  wire [ 1-1:0] sign_extend;
  wire [ 1-1:0] left_justified;
  wire [ 6-1:0] sample_size;
  wire [ 8-1:0] sck_prescaler;
  wire [32-1:0] avg_threshold;
  wire [ 1-1:0] avg_flag;
  wire [ 1-1:0] avg_en;
  wire [ 1-1:0] avg_sel;
  wire [32-1:0] zcr_threshold;
  wire [ 1-1:0] zcr_flag;
  wire [ 1-1:0] zcr_en;
  wire [ 1-1:0] zcr_sel;
  wire [ 1-1:0] vad_flag;
  wire [ 2-1:0] channels;
  wire [ 1-1:0] en;

  // Register Definitions
  wire [32-1:0] RXDATA_WIRE;

  reg  [   7:0] PR_REG;
  assign sck_prescaler = PR_REG;
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) PR_REG <= 0;
    else if (apb_we & (PADDR[16-1:0] == PR_REG_OFFSET)) PR_REG <= PWDATA[8-1:0];

  reg [31:0] AVGT_REG;
  assign avg_threshold = AVGT_REG;
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) AVGT_REG <= 0;
    else if (apb_we & (PADDR[16-1:0] == AVGT_REG_OFFSET)) AVGT_REG <= PWDATA[32-1:0];

  reg [31:0] ZCRT_REG;
  assign zcr_threshold = ZCRT_REG;
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) ZCRT_REG <= 0;
    else if (apb_we & (PADDR[16-1:0] == ZCRT_REG_OFFSET)) ZCRT_REG <= PWDATA[32-1:0];

  reg [3:0] CTRL_REG;
  assign en = CTRL_REG[0 : 0];
  assign fifo_en = CTRL_REG[1 : 1];
  assign avg_en = CTRL_REG[2 : 2];
  assign zcr_en = CTRL_REG[3 : 3];
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) CTRL_REG <= 'h0;
    else if (apb_we & (PADDR[16-1:0] == CTRL_REG_OFFSET)) CTRL_REG <= PWDATA[4-1:0];

  reg [11:0] CFG_REG;
  assign channels = CFG_REG[1 : 0];
  assign sign_extend = CFG_REG[2 : 2];
  assign left_justified = CFG_REG[3 : 3];
  assign sample_size = CFG_REG[9 : 4];
  assign avg_sel = CFG_REG[10 : 10];
  assign zcr_sel = CFG_REG[11 : 11];
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) CFG_REG <= 'h201;
    else if (apb_we & (PADDR[16-1:0] == CFG_REG_OFFSET)) CFG_REG <= PWDATA[12-1:0];

  wire [AW-1:0] RX_FIFO_LEVEL_WIRE;
  assign RX_FIFO_LEVEL_WIRE[(AW-1) : 0] = fifo_level;

  reg [AW-1:0] RX_FIFO_THRESHOLD_REG;
  assign fifo_level_threshold = RX_FIFO_THRESHOLD_REG[(AW-1) : 0];
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) RX_FIFO_THRESHOLD_REG <= 0;
    else if (apb_we & (PADDR[16-1:0] == RX_FIFO_THRESHOLD_REG_OFFSET))
      RX_FIFO_THRESHOLD_REG <= PWDATA[AW-1:0];

  reg [0:0] RX_FIFO_FLUSH_REG;
  assign fifo_flush = RX_FIFO_FLUSH_REG[0 : 0];
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) RX_FIFO_FLUSH_REG <= 0;
    else if (apb_we & (PADDR[16-1:0] == RX_FIFO_FLUSH_REG_OFFSET))
      RX_FIFO_FLUSH_REG <= PWDATA[1-1:0];
    else RX_FIFO_FLUSH_REG <= 1'h0 & RX_FIFO_FLUSH_REG;

  localparam GCLK_REG_OFFSET = 16'hFF10;
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) GCLK_REG <= 0;
    else if (apb_we & (PADDR[16-1:0] == GCLK_REG_OFFSET)) GCLK_REG <= PWDATA[1-1:0];

  reg  [  5:0] IM_REG;
  reg  [  5:0] IC_REG;
  reg  [  5:0] RIS_REG;

  wire [6-1:0] MIS_REG = RIS_REG & IM_REG;
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) IM_REG <= 0;
    else if (apb_we & (PADDR[16-1:0] == IM_REG_OFFSET)) IM_REG <= PWDATA[6-1:0];
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) IC_REG <= 6'b0;
    else if (apb_we & (PADDR[16-1:0] == IC_REG_OFFSET)) IC_REG <= PWDATA[6-1:0];
    else IC_REG <= 6'd0;

  wire [0:0] FIFOE = fifo_empty;
  wire [0:0] FIFOA = fifo_level_above;
  wire [0:0] FIFOF = fifo_full;
  wire [0:0] AVGF = avg_flag;
  wire [0:0] ZCRF = zcr_flag;
  wire [0:0] VADF = vad_flag;

  integer _i_;
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) RIS_REG <= 0;
    else begin
      for (_i_ = 0; _i_ < 1; _i_ = _i_ + 1) begin
        if (IC_REG[_i_]) RIS_REG[_i_] <= 1'b0;
        else if (FIFOE[_i_-0] == 1'b1) RIS_REG[_i_] <= 1'b1;
      end
      for (_i_ = 1; _i_ < 2; _i_ = _i_ + 1) begin
        if (IC_REG[_i_]) RIS_REG[_i_] <= 1'b0;
        else if (FIFOA[_i_-1] == 1'b1) RIS_REG[_i_] <= 1'b1;
      end
      for (_i_ = 2; _i_ < 3; _i_ = _i_ + 1) begin
        if (IC_REG[_i_]) RIS_REG[_i_] <= 1'b0;
        else if (FIFOF[_i_-2] == 1'b1) RIS_REG[_i_] <= 1'b1;
      end
      for (_i_ = 3; _i_ < 4; _i_ = _i_ + 1) begin
        if (IC_REG[_i_]) RIS_REG[_i_] <= 1'b0;
        else if (AVGF[_i_-3] == 1'b1) RIS_REG[_i_] <= 1'b1;
      end
      for (_i_ = 4; _i_ < 5; _i_ = _i_ + 1) begin
        if (IC_REG[_i_]) RIS_REG[_i_] <= 1'b0;
        else if (ZCRF[_i_-4] == 1'b1) RIS_REG[_i_] <= 1'b1;
      end
      for (_i_ = 5; _i_ < 6; _i_ = _i_ + 1) begin
        if (IC_REG[_i_]) RIS_REG[_i_] <= 1'b0;
        else if (VADF[_i_-5] == 1'b1) RIS_REG[_i_] <= 1'b1;
      end
    end

  assign IRQ = |MIS_REG;

  reg [0:0] _sdi_reg_[1:0];
  wire _sdi_w_ = _sdi_reg_[1];
  always @(posedge PCLK or negedge PRESETn)
    if (PRESETn == 0) begin
      _sdi_reg_[0] <= 'b0;
      _sdi_reg_[1] <= 'b0;
    end else begin
      _sdi_reg_[0] <= sdi;
      _sdi_reg_[1] <= _sdi_reg_[0];
    end
  EF_I2S #(
      .DW(DW),
      .AW(AW)
  ) instance_to_wrap (
      .clk(clk),
      .rst_n(rst_n),
      .fifo_en(fifo_en),
      .fifo_rd(fifo_rd),
      .fifo_level_threshold(fifo_level_threshold),
      .fifo_flush(fifo_flush),
      .fifo_full(fifo_full),
      .fifo_empty(fifo_empty),
      .fifo_level(fifo_level),
      .fifo_level_above(fifo_level_above),
      .fifo_rdata(fifo_rdata),
      .sign_extend(sign_extend),
      .left_justified(left_justified),
      .sample_size(sample_size),
      .sck_prescaler(sck_prescaler),
      .avg_threshold(avg_threshold),
      .avg_flag(avg_flag),
      .avg_en(avg_en),
      .avg_sel(avg_sel),
      .zcr_threshold(zcr_threshold),
      .zcr_flag(zcr_flag),
      .zcr_en(zcr_en),
      .zcr_sel(zcr_sel),
      .vad_flag(vad_flag),
      .channels(channels),
      .en(en),
      .ws(ws),
      .sck(sck),
      .sdi(_sdi_w_)
  );

  assign	PRDATA = 
			(PADDR[16-1:0] == RXDATA_REG_OFFSET)	? RXDATA_WIRE :
			(PADDR[16-1:0] == PR_REG_OFFSET)	? PR_REG :
			(PADDR[16-1:0] == AVGT_REG_OFFSET)	? AVGT_REG :
			(PADDR[16-1:0] == ZCRT_REG_OFFSET)	? ZCRT_REG :
			(PADDR[16-1:0] == CTRL_REG_OFFSET)	? CTRL_REG :
			(PADDR[16-1:0] == CFG_REG_OFFSET)	? CFG_REG :
			(PADDR[16-1:0] == RX_FIFO_LEVEL_REG_OFFSET)	? RX_FIFO_LEVEL_WIRE :
			(PADDR[16-1:0] == RX_FIFO_THRESHOLD_REG_OFFSET)	? RX_FIFO_THRESHOLD_REG :
			(PADDR[16-1:0] == RX_FIFO_FLUSH_REG_OFFSET)	? RX_FIFO_FLUSH_REG :
			(PADDR[16-1:0] == IM_REG_OFFSET)	? IM_REG :
			(PADDR[16-1:0] == MIS_REG_OFFSET)	? MIS_REG :
			(PADDR[16-1:0] == RIS_REG_OFFSET)	? RIS_REG :
			(PADDR[16-1:0] == GCLK_REG_OFFSET)	? GCLK_REG :
			32'hDEADBEEF;

  assign PREADY = 1'b1;

  assign RXDATA_WIRE = fifo_rdata;
  assign fifo_rd = (apb_re & (PADDR[16-1:0] == RXDATA_REG_OFFSET));
endmodule
