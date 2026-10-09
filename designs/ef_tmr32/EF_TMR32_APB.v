`ifdef PRINT_LICENSE
/*
	Copyright 2024 Efabless Corp

    Author: Efabless Corp (ip_admin@efabless.com )
	
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
`endif // PRINT_LICENSE

`timescale			    1ns/1ps
`default_nettype		none

`define     PED(clk, sig, pulse)    reg last_``sig``; always @(posedge clk) last_``sig`` <= sig; assign pulse = sig & ~last_``sig``;
`define     NED(clk, sig, pulse)    reg last_``sig``; always @(posedge clk) last_``sig`` <= sig; assign pulse = ~sig & last_``sig``;

/*
    Brute-force Synchronizer
*/
// From efabless/EF_IP_UTIL hdl/ef_util_lib.v (commit a2b3fb6): the two modules EF_TMR32 needs.
module ef_util_ped (
    input wire clk,
    input wire in,
    output wire out
);
    reg last_in; 
    always @(posedge clk) 
	last_in <= in; 
    assign out = in & ~last_in;
endmodule

/*
    A negative edge detector
*/

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

// From efabless/EF_TMR32 hdl/rtl/EF_TMR32.v (commit 767f7b0)
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
`timescale          1ns/1ps
`default_nettype    none                                                   

module EF_TMR32 #(parameter PRW = 16,
                                PWM_FAULT_CLR_C0 = 16'hA539,
                                PWM_FAULT_CLR_C1 = 16'hA953 
)
(
    input   wire            clk,
    input   wire            rst_n,

    input   wire            tmr_en,
    input   wire            tmr_start,
    input   wire            pwm0_en,
    input   wire            pwm1_en,
    input   wire [31:0]     tmr_reload,
    input   wire [31:0]     cmpx,
    input   wire [31:0]     cmpy,
    input   wire [PRW-1:0]  prescaler,
    input   wire [ 2:0]     tmr_cfg,     // [2]: Periodic/OneShot; [1:0]: 10: Up, 01: Down, 11: Up/Down
    input   wire [11:0]     pwm0_cfg,
    input   wire [11:0]     pwm1_cfg,
    input   wire            pwm0_inv,
    input   wire            pwm1_inv,
    input   wire [ 7:0]     pwm_dt,
    input   wire [15:0]     pwm_fault_clr,
    input   wire            pwm_dt_en,

    output  wire [31:0]     tmr,
    output  wire            matchx_flag,
    output  wire            matchy_flag,
    output  wire            timeout_flag,
    
    input   wire            pwm_fault,
    output  wire            pwm0,
    output  wire            pwm1
);

    wire [1:0]      tmr_mode        = tmr_cfg[1:0];
    wire            tmr_periodic    = tmr_cfg[2];

    reg [31:0]      tmr_reg;
    reg [PRW-1:0]   pr_reg;

    wire            tmr_clr;

    wire            tmr_en_pulse = tmr_clr;
    reg             tmr_run;

    reg             fault_reg;

    ef_util_ped TMREN_PE (
        .clk(clk),
        .in(tmr_en),
        .out(tmr_clr)
    );

    wire tick = (pr_reg == 0);
    always @(posedge clk, negedge rst_n)
                                                    if(!rst_n) pr_reg <= 1;
                                                    else
        if(tmr_en)
            if(tick) pr_reg <= prescaler; 
            else pr_reg <= pr_reg - 1; 
        else
            pr_reg <= prescaler ;

    reg         tmr_dir;        // 1: Up, 0: Down
    wire        tmr_eq_reload       =   (tmr == tmr_reload);
    wire        tmr_eq_zero         =   (tmr == 0);
    wire        tmr_eq_reload_m_1   =   (tmr == (tmr_reload - 1));
    wire        tmr_eq_one          =   (tmr == 1);
    wire        tmr_eq_cmpx         =   (tmr == cmpx);
    wire        tmr_eq_cmpy         =   (tmr == cmpy);

    always @(posedge clk, negedge rst_n)
                                                    if(!rst_n) tmr_run <= 0;
                                                    else
        if(tmr_en_pulse)
            tmr_run <= 1;
        else if(~tmr_periodic & tick)
            if((tmr_mode[0] == 1'b1) & tmr_eq_one & ~tmr_dir)
                tmr_run <= 0;
            else if((tmr_mode == 2'b10) & tmr_eq_reload_m_1 & tmr_dir)
                tmr_run <= 1;


    // The timer
    reg [31:0]  tmr_reg_next;

    always@* begin
        tmr_reg_next = tmr_reg;
        if(~tmr_run)
            tmr_reg_next = tmr_reg;    
        else if(tmr_start & (tmr_mode == 2'b01))
            tmr_reg_next = tmr_reload;
        else if(tmr_start & (tmr_mode == 2'b10))
            tmr_reg_next = 0;
        else if(tmr_start & (tmr_mode == 2'b11))
            tmr_reg_next = 0;
        else if(tmr_mode == 2'b11) begin
            if(tmr_dir)
                tmr_reg_next = tmr_reg + 1;
            else
                tmr_reg_next = tmr_reg - 1; 
        end
        else if((tmr_mode == 2'b01))
            if(tmr_eq_zero)
                tmr_reg_next = tmr_periodic ? tmr_reload : tmr_reg;
            else
                tmr_reg_next = tmr_reg_next - 1;    
        else if((tmr_mode == 2'b10))
            if(tmr_eq_reload)
                tmr_reg_next = tmr_periodic ? 0 : tmr_reg;
            else
                tmr_reg_next = tmr_reg_next + 1;
    end

    always @(posedge clk, negedge rst_n)
                                                    if(!rst_n) tmr_reg <= 0;
                                                    else
    if(tmr_en)
        if(tmr_clr)
            if(tmr_mode == 2'b01)
                tmr_reg <= tmr_reload;
            else
                tmr_reg <= 0;
        else 
            if(tick)
                tmr_reg <=  tmr_reg_next;

    // The counting direction flag
    always @(posedge clk, negedge rst_n)
                                                    if(!rst_n) tmr_dir <= 1;
                                                    else
        if(tmr_clr)
            if(tmr_mode == 2'b01)
                tmr_dir <= 0;
            else
                tmr_dir <= 1;
        else if(tick)
            if(tmr_mode == 2'b11) begin
                if(tmr_eq_one & ~tmr_dir) 
                    tmr_dir <= 1;
                else if(tmr_eq_reload_m_1 & tmr_dir)
                    tmr_dir <= 0;
            end
            else if(tmr_mode == 2'b10)
                tmr_dir <= 1'b1;
            else if(tmr_mode == 2'b01)
                tmr_dir <= 1'b0;
            else
                tmr_dir <= 1'b1;

    // PWM Generator
    function pwm_action(input [1:0] action, input sig);
        case (action)
            2'b00: pwm_action = sig;
            2'b01: pwm_action = 0;
            2'b10: pwm_action = 1;
            2'b11: pwm_action = ~sig; 
        endcase
    endfunction 

    reg     pwm0_reg, pwm0_reg_next;
    reg     pwm1_reg, pwm1_reg_next;

    always @* begin
        casez({tmr_dir, tmr_eq_zero, tmr_eq_cmpx, tmr_eq_cmpy, tmr_eq_reload})
            5'b?_1_00_0 : pwm0_reg_next = pwm_action(pwm0_cfg[ 1: 0], pwm0_reg);    // U/D, 0
            5'b1_0_10_0 : pwm0_reg_next = pwm_action(pwm0_cfg[ 3: 2], pwm0_reg);    // U, CMPX
            5'b1_0_01_0 : pwm0_reg_next = pwm_action(pwm0_cfg[ 5: 4], pwm0_reg);    // U, CMPY
            5'b?_0_00_1 : pwm0_reg_next = pwm_action(pwm0_cfg[ 7: 6], pwm0_reg);    // U/D, RELOAD
            5'b0_0_01_0 : pwm0_reg_next = pwm_action(pwm0_cfg[ 9: 8], pwm0_reg);    // D, CMPY
            5'b0_0_10_0 : pwm0_reg_next = pwm_action(pwm0_cfg[11:10], pwm0_reg);    // D, CMPX
            default     : pwm0_reg_next = pwm0_reg;
        endcase        
    end

    always @* begin
        casez({tmr_dir, tmr_eq_zero, tmr_eq_cmpx, tmr_eq_cmpy, tmr_eq_reload})
            5'b?_1_00_0 : pwm1_reg_next = pwm_action(pwm1_cfg[ 1: 0], pwm1_reg);    // U/D, 0
            5'b1_0_10_0 : pwm1_reg_next = pwm_action(pwm1_cfg[ 3: 2], pwm1_reg);    // U, CMPX
            5'b1_0_01_0 : pwm1_reg_next = pwm_action(pwm1_cfg[ 5: 4], pwm1_reg);    // U, CMPY
            5'b?_0_00_1 : pwm1_reg_next = pwm_action(pwm1_cfg[ 7: 6], pwm1_reg);    // U/D, RELOAD
            5'b0_0_01_0 : pwm1_reg_next = pwm_action(pwm1_cfg[ 9: 8], pwm1_reg);    // D, CMPY
            5'b0_0_10_0 : pwm1_reg_next = pwm_action(pwm1_cfg[11:10], pwm1_reg);    // D, CMPX
            default     : pwm1_reg_next = pwm1_reg;
        endcase        
    end

    always @(posedge clk, negedge rst_n)
                                                    if(!rst_n) pwm0_reg <= 0;
                                                    else
        if(pwm0_en & tick)
            if(pwm_fault)
                pwm0_reg <= 0;
            else
                pwm0_reg <= pwm0_reg_next;

    always @(posedge clk, negedge rst_n)
                                                    if(!rst_n) pwm1_reg <= 0;
                                                    else
        if(pwm1_en & tick)
            if(pwm_fault)
                pwm1_reg <= 0;
            else
                pwm1_reg <= pwm1_reg_next;


    // Dead time insertion
    reg pwm0_delayed;
    reg [7:0] dly_cntr;
    always @(posedge clk, negedge rst_n)
                                                    if(!rst_n) dly_cntr <= 0;
                                                    else
        if(tick)
            if(dly_cntr == 0)
                dly_cntr <= pwm_dt;
            else 
                dly_cntr <= dly_cntr - 1;
                
    always @(posedge clk, negedge rst_n)
                                                    if(!rst_n) pwm0_delayed <= 0;
                                                    else
        if(tick)
            if(dly_cntr == 0)
                pwm0_delayed <= pwm0_reg;

    reg pwm0_w_dt, pwm1_w_dt;
    always @(posedge clk, negedge rst_n)
                                                    if(!rst_n) pwm0_w_dt <= 0;
                                                    else
        pwm0_w_dt <= pwm_dt_en ? (pwm0_delayed & pwm0_reg) : pwm0_reg;

    always @(posedge clk, negedge rst_n)
                                                    if(!rst_n) pwm1_w_dt <= 0;
                                                    else
        pwm1_w_dt <= pwm_dt_en ? (~pwm0_delayed & ~pwm0_reg) : pwm1_reg;
    
    // PWM Fault Handeling
    reg fault_clr_reg;
    always @(posedge clk, negedge rst_n)
                                                    if(!rst_n) fault_clr_reg <= 0;
                                                    else
        if(pwm_fault_clr == PWM_FAULT_CLR_C0)
            fault_clr_reg <= 1;
        else if(pwm_fault_clr == PWM_FAULT_CLR_C1)
            fault_clr_reg <= 0;
    always @(posedge clk, negedge rst_n)
                                                    if(!rst_n) fault_reg <= 0;
                                                    else
        if(pwm_fault)
            fault_reg <= 1;
        else if( fault_clr_reg & (pwm_fault_clr == PWM_FAULT_CLR_C1) )
            fault_reg <= 0;
            
    // Connect the outputs
    assign  tmr             =   tmr_reg;
    assign  pwm0            =   pwm0_w_dt ^ pwm0_inv && ~fault_reg && pwm0_en;
    assign  pwm1            =   pwm1_w_dt ^ pwm1_inv && ~fault_reg && pwm1_en;
    assign  matchx_flag     =   tmr_eq_cmpx;
    assign  matchy_flag     =   tmr_eq_cmpy;
    assign  timeout_flag    =   tmr_dir ? tmr_eq_reload : tmr_eq_zero;
    
endmodule

// From efabless/EF_TMR32 hdl/rtl/bus_wrappers/EF_TMR32_APB.v (commit 767f7b0)
/*
	Copyright 2024 Efabless Corp.

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

`timescale 1ns / 1ps
`default_nettype none

module EF_TMR32_APB #(
    parameter PRW = 16
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
    output wire [1-1:0] pwm0,
    output wire [1-1:0] pwm1,
    input  wire [1-1:0] pwm_fault
);

  localparam TMR_REG_OFFSET = 16'h0000;
  localparam RELOAD_REG_OFFSET = 16'h0004;
  localparam PR_REG_OFFSET = 16'h0008;
  localparam CMPX_REG_OFFSET = 16'h000C;
  localparam CMPY_REG_OFFSET = 16'h0010;
  localparam CTRL_REG_OFFSET = 16'h0014;
  localparam CFG_REG_OFFSET = 16'h0018;
  localparam PWM0CFG_REG_OFFSET = 16'h001C;
  localparam PWM1CFG_REG_OFFSET = 16'h0020;
  localparam PWMDT_REG_OFFSET = 16'h0024;
  localparam PWMFC_REG_OFFSET = 16'h0028;
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

  wire           clk = clk_g;
  wire           rst_n = PRESETn;

  wire           apb_valid = PSEL & PENABLE;
  wire           apb_we = PWRITE & apb_valid;
  wire           apb_re = ~PWRITE & apb_valid;

  wire [  1-1:0] tmr_en;
  wire [  1-1:0] tmr_start;
  wire [  1-1:0] pwm0_en;
  wire [  1-1:0] pwm1_en;
  wire [ 32-1:0] tmr_reload;
  wire [ 32-1:0] cmpx;
  wire [ 32-1:0] cmpy;
  wire [PRW-1:0] prescaler;
  wire [  3-1:0] tmr_cfg;
  wire [ 12-1:0] pwm0_cfg;
  wire [ 12-1:0] pwm1_cfg;
  wire [  1-1:0] pwm0_inv;
  wire [  1-1:0] pwm1_inv;
  wire [  8-1:0] pwm_dt;
  wire [ 16-1:0] pwm_fault_clr;
  wire [  1-1:0] pwm_dt_en;
  wire [ 32-1:0] tmr;
  wire [  1-1:0] matchx_flag;
  wire [  1-1:0] matchy_flag;
  (* keep *)wire [  1-1:0] timeout_flag;

  // Register Definitions
  wire [ 32-1:0] TMR_WIRE;
  assign TMR_WIRE = tmr;

  reg [31:0] RELOAD_REG;
  assign tmr_reload = RELOAD_REG;
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) RELOAD_REG <= 0;
    else if (apb_we & (PADDR[16-1:0] == RELOAD_REG_OFFSET)) RELOAD_REG <= PWDATA[32-1:0];

  reg [PRW-1:0] PR_REG;
  assign prescaler = PR_REG;
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) PR_REG <= 'h0;
    else if (apb_we & (PADDR[16-1:0] == PR_REG_OFFSET)) PR_REG <= PWDATA[PRW-1:0];

  reg [31:0] CMPX_REG;
  assign cmpx = CMPX_REG;
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) CMPX_REG <= 0;
    else if (apb_we & (PADDR[16-1:0] == CMPX_REG_OFFSET)) CMPX_REG <= PWDATA[32-1:0];

  reg [31:0] CMPY_REG;
  assign cmpy = CMPY_REG;
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) CMPY_REG <= 0;
    else if (apb_we & (PADDR[16-1:0] == CMPY_REG_OFFSET)) CMPY_REG <= PWDATA[32-1:0];

  reg [6:0] CTRL_REG;
  assign tmr_en = CTRL_REG[0 : 0];
  assign tmr_start = CTRL_REG[1 : 1];
  assign pwm0_en = CTRL_REG[2 : 2];
  assign pwm1_en = CTRL_REG[3 : 3];
  assign pwm_dt_en = CTRL_REG[4 : 4];
  assign pwm0_inv = CTRL_REG[5 : 5];
  assign pwm1_inv = CTRL_REG[6 : 6];
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) CTRL_REG <= 0;
    else if (apb_we & (PADDR[16-1:0] == CTRL_REG_OFFSET)) CTRL_REG <= PWDATA[7-1:0];

  reg [2:0] CFG_REG;
  assign tmr_cfg = CFG_REG;
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) CFG_REG <= 0;
    else if (apb_we & (PADDR[16-1:0] == CFG_REG_OFFSET)) CFG_REG <= PWDATA[3-1:0];

  reg [11:0] PWM0CFG_REG;
  assign pwm0_cfg = PWM0CFG_REG;
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) PWM0CFG_REG <= 0;
    else if (apb_we & (PADDR[16-1:0] == PWM0CFG_REG_OFFSET)) PWM0CFG_REG <= PWDATA[12-1:0];

  reg [15:0] PWM1CFG_REG;
  assign pwm1_cfg = PWM1CFG_REG;
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) PWM1CFG_REG <= 0;
    else if (apb_we & (PADDR[16-1:0] == PWM1CFG_REG_OFFSET)) PWM1CFG_REG <= PWDATA[16-1:0];

  reg [7:0] PWMDT_REG;
  assign pwm_dt = PWMDT_REG;
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) PWMDT_REG <= 0;
    else if (apb_we & (PADDR[16-1:0] == PWMDT_REG_OFFSET)) PWMDT_REG <= PWDATA[8-1:0];

  reg [15:0] PWMFC_REG;
  assign pwm_fault_clr = PWMFC_REG;
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) PWMFC_REG <= 0;
    else if (apb_we & (PADDR[16-1:0] == PWMFC_REG_OFFSET)) PWMFC_REG <= PWDATA[16-1:0];

  localparam GCLK_REG_OFFSET = 16'hFF10;
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) GCLK_REG <= 0;
    else if (apb_we & (PADDR[16-1:0] == GCLK_REG_OFFSET)) GCLK_REG <= PWDATA[1-1:0];

  reg  [  2:0] IM_REG;
  reg  [  2:0] IC_REG;
  reg  [  2:0] RIS_REG;

  wire [3-1:0] MIS_REG = RIS_REG & IM_REG;
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) IM_REG <= 0;
    else if (apb_we & (PADDR[16-1:0] == IM_REG_OFFSET)) IM_REG <= PWDATA[3-1:0];
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) IC_REG <= 3'b0;
    else if (apb_we & (PADDR[16-1:0] == IC_REG_OFFSET)) IC_REG <= PWDATA[3-1:0];
    else IC_REG <= 3'd0;

  wire [0:0] TO = timeout_flag;
  wire [0:0] MX = matchx_flag;
  wire [0:0] MY = matchy_flag;

  integer _i_;
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) RIS_REG <= 0;
    else begin
      for (_i_ = 0; _i_ < 1; _i_ = _i_ + 1) begin
        if (IC_REG[_i_]) RIS_REG[_i_] <= 1'b0;
        else if (TO[_i_-0] == 1'b1) RIS_REG[_i_] <= 1'b1;
      end
      for (_i_ = 1; _i_ < 2; _i_ = _i_ + 1) begin
        if (IC_REG[_i_]) RIS_REG[_i_] <= 1'b0;
        else if (MX[_i_-1] == 1'b1) RIS_REG[_i_] <= 1'b1;
      end
      for (_i_ = 2; _i_ < 3; _i_ = _i_ + 1) begin
        if (IC_REG[_i_]) RIS_REG[_i_] <= 1'b0;
        else if (MY[_i_-2] == 1'b1) RIS_REG[_i_] <= 1'b1;
      end
    end

  assign IRQ = |MIS_REG;

  EF_TMR32 #(
      .PRW(PRW)
  ) instance_to_wrap (
      .clk(clk),
      .rst_n(rst_n),
      .tmr_en(tmr_en),
      .tmr_start(tmr_start),
      .pwm0_en(pwm0_en),
      .pwm1_en(pwm1_en),
      .tmr_reload(tmr_reload),
      .cmpx(cmpx),
      .cmpy(cmpy),
      .prescaler(prescaler),
      .tmr_cfg(tmr_cfg),
      .pwm0_cfg(pwm0_cfg),
      .pwm1_cfg(pwm1_cfg),
      .pwm0_inv(pwm0_inv),
      .pwm1_inv(pwm1_inv),
      .pwm_dt(pwm_dt),
      .pwm_fault_clr(pwm_fault_clr),
      .pwm_dt_en(pwm_dt_en),
      .tmr(tmr),
      .matchx_flag(matchx_flag),
      .matchy_flag(matchy_flag),
      .timeout_flag(timeout_flag),
      .pwm0(pwm0),
      .pwm1(pwm1),
      .pwm_fault(pwm_fault)
  );

  assign	PRDATA = 
			(PADDR[16-1:0] == TMR_REG_OFFSET)	? TMR_WIRE :
			(PADDR[16-1:0] == RELOAD_REG_OFFSET)	? RELOAD_REG :
			(PADDR[16-1:0] == PR_REG_OFFSET)	? PR_REG :
			(PADDR[16-1:0] == CMPX_REG_OFFSET)	? CMPX_REG :
			(PADDR[16-1:0] == CMPY_REG_OFFSET)	? CMPY_REG :
			(PADDR[16-1:0] == CTRL_REG_OFFSET)	? CTRL_REG :
			(PADDR[16-1:0] == CFG_REG_OFFSET)	? CFG_REG :
			(PADDR[16-1:0] == PWM0CFG_REG_OFFSET)	? PWM0CFG_REG :
			(PADDR[16-1:0] == PWM1CFG_REG_OFFSET)	? PWM1CFG_REG :
			(PADDR[16-1:0] == PWMDT_REG_OFFSET)	? PWMDT_REG :
			(PADDR[16-1:0] == PWMFC_REG_OFFSET)	? PWMFC_REG :
			(PADDR[16-1:0] == IM_REG_OFFSET)	? IM_REG :
			(PADDR[16-1:0] == MIS_REG_OFFSET)	? MIS_REG :
			(PADDR[16-1:0] == RIS_REG_OFFSET)	? RIS_REG :
			(PADDR[16-1:0] == GCLK_REG_OFFSET)	? GCLK_REG :
			32'hDEADBEEF;

  assign PREADY = 1'b1;

endmodule
