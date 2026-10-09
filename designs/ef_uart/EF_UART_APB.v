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
// From efabless/EF_IP_UTIL hdl/ef_util_lib.v (commit a2b3fb6): the modules EF_UART needs.
module ef_util_sync #(parameter NUM_STAGES = 2) (
    input wire clk,
    input wire in,
    output wire out
);

    reg [NUM_STAGES-1:0] sync;

    always @(posedge clk)
        sync <= {sync[NUM_STAGES-2:0], in};

    assign out = sync[NUM_STAGES-1];

endmodule

module ef_util_ticker #(parameter W=8) (
    input   wire            clk, 
    input   wire            rst_n,
    input   wire            en,
    input   wire [W-1:0]    clk_div,
    output  wire            tick
);

    reg [W-1:0] counter;
    wire        counter_is_zero = (counter == 'b0);
    wire        tick_w;
    reg         tick_reg;

    always @(posedge clk, negedge rst_n)
        if(~rst_n)
            counter <=  'b0;
        else if(en) 
            if(counter_is_zero)
                counter <=  clk_div;
            else
                counter <=  counter - 'b1; 

	assign tick_w = (clk_div == 'b0)  ?   1'b1 : counter_is_zero;

    always @(posedge clk or negedge rst_n)
        if(!rst_n)
            tick_reg <= 1'b0;
        else if(en)
            tick_reg <= tick_w;
        else
            tick_reg <= 0;

    assign tick = tick_reg;

endmodule

module ef_util_glitch_filter #(parameter N = 8, CLKDIV = 8'd1) (
    input   wire    clk,
    input   wire    rst_n,
    input   wire    in,
    input   wire    en,
    output  reg     out
);

    reg [N-1:0] shifter;
    wire        tick;

    ef_util_ticker ticker (
        .clk(clk),
        .rst_n(rst_n),
	.en(en),
        .clk_div(CLKDIV),
        .tick(tick)
    );

    always @(posedge clk, negedge rst_n)
        if(!rst_n)
            shifter <= 'b0;
        else if(tick)
            shifter <= {shifter[N-2:0], in};

    wire all_ones   = & shifter;
    wire all_zeros  = ~| shifter;

    always @(posedge clk, negedge rst_n)
        if(!rst_n)
            out <= 1'b0;
        else
            if(all_ones) 
                out <= 1'b1;
            else if(all_zeros) 
                out <= 1'b0;
endmodule

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
// From efabless/EF_UART hdl/rtl/EF_UART.v (commit 793ec5a)
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
    A Universal Asynchronous Receiver/Transmitter (UART) 
    - Programmable frame format
        - Data: 5-9 bits
        - Parity: None, Odd, Even, or Sticky at 0/1
    - TX and RX FIFOs with programmable thresholds
    - 16-bit prescaler (PR) for programable baud rate generation
    - Baudrate = CLK/((PR+1)*NUM_SAMPLES)
    - RX synchronizer
    - RX Glich Filter
    - Interrupt Sources:
        + TX fifo not full
        + RX fifo not empty
        + RX fifo level exceeded the threshold
        + TX fifo level is below the threshold
        + Framing Error
        + Parity Error
        + Break is observed
        + Timeout: Nothing received for the time of 4 frames!
        + Overrun
        + Receiving a specific frame
*/

`timescale			1ns/1ps
`default_nettype		none


module EF_UART #(parameter  MDW = 9,        // Max data size/width
                                FAW = 4,        // FIFO Address width; Depth=2^AW
                                SC = 8,         // Number of samples per bit/baud
                                GFLEN = 8       // Length (number of stages) of the glitch filter
) (
    input   wire            clk,
    input   wire            rst_n,
    
    input   wire [15:0]     prescaler,
    input   wire            en,
    input   wire            tx_en,
    input   wire            rx_en,
    input   wire            rd,
    input   wire            wr,
    input   wire [MDW-1:0]  wdata,
    input   wire [3:0]      data_size,          // 5 - 9
    input   wire            stop_bits_count,    // 0: 1, 1: 2
    input   wire [2:0]      parity_type,        // 000: None, 001: odd, 010: even, 100: Sticky 0, 101: Sticky 1
    input   wire [3:0]      txfifotr,
    input   wire [3:0]      rxfifotr,
    input   wire [MDW-1:0]  match_data,
    input   wire [5:0]      timeout_bits,
    input   wire            loopback_en,
    input   wire            glitch_filter_en,
    input   wire            tx_fifo_flush,
    input   wire            rx_fifo_flush,
            
    output  wire            tx_empty,
    output  wire            tx_full,
    output  wire [FAW-1:0]  tx_level,
    output  wire            tx_level_below,
    output  wire [MDW-1:0]  rdata,
    output  wire            rx_empty,
    output  wire            rx_full,
    output  wire [FAW-1:0]  rx_level,
    output  wire            rx_level_above,

    output  wire            break_flag,
    output  wire            match_flag,
    output  wire            frame_error_flag,
    output  wire            parity_error_flag,
    output  wire            overrun_flag,
    output  wire            timeout_flag,

    input   wire            rx,
    output  wire            tx
);

    (* keep *) wire        tx_done;
    (* keep *) wire        rx_done;

    wire        b_tick;

    wire [MDW-1:0]  tx_data;
    wire [MDW-1:0]  rx_data;
    
    parameter FIFO_DW = MDW;

    wire        rx_synched;
    wire        rx_filtered;
    wire        rx_in;

    ef_util_sync rx_sync (
        .clk(clk),
        .in(rx),
        .out(rx_synched)
    );

    ef_util_glitch_filter #(.N(GFLEN)) rx_glitch_filter (
        .clk(clk),
        .rst_n(rst_n),
        .en(glitch_filter_en),
        .in(rx_synched),
        .out(rx_filtered)
    );

    assign rx_in =  loopback_en         ? tx            : 
                    glitch_filter_en    ? rx_filtered   : 
                    rx_synched;

    BAUDGEN buad_gen (
        .clk(clk),
        .rst_n(rst_n),
        .prescale(prescaler),
        .en(en),
        .baudtick(b_tick)
    );
  
    ef_util_fifo #(.DW(FIFO_DW), .AW(FAW)) fifo_tx (
        .clk(clk),
        .rst_n(rst_n),
        .rd(tx_done),
        .wr(wr),
        .wdata(wdata),
        .empty(tx_empty),
        .full(tx_full),
        .rdata(tx_data),
        .level(tx_level),
        .flush(tx_fifo_flush)
    );

    UART_TX #(.MDW(MDW), .NUM_SAMPLES(SC)) uart_tx (
        .clk(clk),
        .resetn(rst_n),
        .tx_start(~tx_empty),
        .b_tick(b_tick & tx_en),
        .data_size(data_size),
        .parity_type(parity_type),
        .stop_bits_count(stop_bits_count),
        .d_in(tx_data),
        .tx_done(tx_done),
        .tx(tx)
    );

    ef_util_fifo #(.DW(FIFO_DW), .AW(FAW)) fifo_rx (
        .clk(clk),
        .rst_n(rst_n),
        .rd(rd),
        .wr(rx_done),
        .wdata(rx_data),
        .empty(rx_empty),
        .full(rx_full),
        .rdata(rdata),
        .level(rx_level),
        .flush(rx_fifo_flush)
    );

    UART_RX #(.MDW(MDW), .NUM_SAMPLES(SC)) uart_rx (
        .clk(clk),
        .resetn(rst_n),
        .b_tick(b_tick & rx_en),
        .data_size(data_size),
        .parity_type(parity_type),
        .stop_bits_count(stop_bits_count),
        .match_data(match_data),
        .rx(rx_in),
        .break_flag(break_flag),
        .match_flag(match_flag),
        .parity_error(parity_error_flag),
        .frame_error(frame_error_flag),
        .rx_done(rx_done),
        .dout(rx_data)
    );

    reg [5:0]   bits_count;
    reg [4:0]   samples_count;
    always @ (posedge clk, negedge rst_n) begin
        if(!rst_n) begin
            bits_count <= 0;
            samples_count <= 0;
        end
        else if(b_tick)
            if(rx_done) bits_count <= 0;
            else if(samples_count == (SC - 1)) begin
                samples_count <= 0;
                if(timeout_flag)
                    bits_count <= 0;
                else
                    bits_count <= bits_count + 1;
            end else
                samples_count <= samples_count + 1'b1;
    end

    assign tx_level_below = (tx_level < txfifotr) & ~tx_full;
    assign rx_level_above = (rx_level > rxfifotr) | rx_full;
    assign overrun_flag = rx_full & rx_done;
    assign timeout_flag = (bits_count == timeout_bits);

endmodule


module BAUDGEN
(
    input   wire        clk,
    input   wire        rst_n,
    input   wire [15:0] prescale, 
    input   wire        en,
    output  wire        baudtick
);

    reg [15:0]  count_reg;
    wire [15:0] count_next;

    //Counter
    always @ (posedge clk, negedge rst_n) begin
        if(!rst_n)
            count_reg <= 0;
        else if(en)
            count_reg <= count_next;
    end

    assign count_next = ((count_reg == prescale) ? 0 : count_reg + 1'b1);
    assign baudtick = ((count_reg == prescale) ? 1'b1 : 1'b0);

endmodule

/*
    UART Receiver
*/
module UART_RX #(parameter NUM_SAMPLES = 16, MDW = 8)(
    input   wire            clk,
    input   wire            resetn,
    input   wire            b_tick,             // Baud generator tick
    input   wire [3:0]      data_size,          // 5 - 9
    input   wire            stop_bits_count,    // 0: 1, 1: 2
    input   wire [2:0]      parity_type,        // 000: None, 001: odd, 010: even, 
                                                // 100: Sticky 0, 101: Sticky 1
    input   wire            rx,                 // RS-232 data port
    input   wire [MDW-1:0]  match_data,
    output  reg             rx_done,            // Transfer completed
    output  wire            parity_error,       // Parity Error
    output  wire            frame_error,        // Framing Error
    output  wire            break_flag,         // Break flag
    output  wire            match_flag,
    output  wire [MDW-1:0]  dout                // Received data
);
    //STATE DEFINES  
    localparam [2:0] idle_st    = 3'b000;
    localparam [2:0] start_st   = 3'b001;
    localparam [2:0] data_st    = 3'b010;
    localparam [2:0] parity_st  = 3'b011;
    localparam [2:0] stop0_st   = 3'b100;
    localparam [2:0] stop1_st   = 3'b101;

    //Internal Signals  
    reg [2:0]   current_state;
    reg [2:0]   next_state;
    reg [3:0]   b_reg;            //baud-rate/over sampling counter
    reg [3:0]   b_next;
    reg [3:0]   count_reg;        //data-bit counter
    reg [3:0]   count_next;
    reg [8:0]   data_reg;         //data register
    reg [8:0]   data_next;
    reg         p_error_reg;
    reg         p_error_next;
    reg         f_error_reg;
    reg         f_error_next;

    //State Machine  
    always @ (posedge clk, negedge resetn) begin
        if(!resetn) begin
            current_state <= idle_st;
            b_reg <= 0;
            count_reg <= 0;
            data_reg <= 0;
            p_error_reg <= 0;
        end else begin
            current_state <= next_state;
            b_reg <= b_next;
            count_reg <= count_next;
            data_reg <= data_next;
            if(current_state == idle_st) 
                p_error_reg <= 0;
            else 
                if(p_error_next) 
                    p_error_reg <= p_error_next;
            if(current_state == idle_st) 
                    f_error_reg <= 0;
                else 
                    if(f_error_next) 
                        f_error_reg <= f_error_next;
        end
    end

    //Next State Logic 
    always @* begin
        next_state = current_state;
        b_next = b_reg;
        count_next = count_reg;
        data_next = data_reg;
        rx_done = 1'b0;
        p_error_next = 1'b0;
        f_error_next = 1'b0;
            
        case(current_state)
            idle_st:
                if(~rx & b_tick)
                begin
                    next_state = start_st;
                    b_next = 0;
                end
                
            start_st:
                if(b_tick)
                    if(b_reg == (NUM_SAMPLES/2 - 1)) begin
                        next_state = data_st;
                        b_next = 0;
                        count_next = 0;
                    end else
                        b_next = b_reg + 1'b1;
                    
            data_st:
                if(b_tick)
                    if(b_reg == (NUM_SAMPLES - 1)) begin
                        b_next = 0;
                        data_next = {rx, data_reg [(MDW-1):1]};
                        if(count_next == (data_size - 1)) 
                            if(parity_type == 3'b000)         
                                next_state = stop0_st;
                            else
                                next_state = parity_st;
                        else
                            count_next = count_reg + 1'b1;
                    end else
                        b_next = b_reg + 1;
            
            parity_st:
                if(b_tick)
                    if(b_reg == (NUM_SAMPLES - 1)) begin
                        b_next = 0;
                        next_state = stop0_st;
                        case (parity_type)
                            3'b001 : //Odd parity
                                if(~^dout != rx) p_error_next = 1;
                            3'b010 : //Even parity
                                if(^dout != rx) p_error_next = 1;
                            3'b100 : //Sticky 0 parity
                                if(1'b0 != rx) p_error_next = 1;
                            3'b101 : //Sticky 1 parity
                                if(1'b1 != rx) p_error_next = 1;
                        endcase
                    end else
                        b_next = b_reg + 1;  
            stop0_st:
                if(b_tick)
                    if(b_reg == (NUM_SAMPLES - 1)) begin 
                        b_next = 0;
                        if(!rx) f_error_next = 1;
                        if(stop_bits_count)         //Two stop bits
                            next_state = stop1_st;
                        else begin                  //One stop bit 
                            next_state = idle_st;
                            rx_done = 1'b1;
                        end
                    end else
                        b_next = b_reg + 1;
            stop1_st:
                if(b_tick)
                    if(b_reg == (NUM_SAMPLES - 1)) begin //Two stop bits
                        b_next = 0;
                        next_state = idle_st;
                        rx_done = 1'b1;
                        if(!rx) f_error_next = 1;
                    end else
                        b_next = b_reg + 1;
        endcase
    end
  
    // Break Detector
    reg [11:0] brk;
    always @ (posedge clk, negedge resetn) begin
        if(!resetn) 
            brk <= 12'hFFF;
        else if(b_tick)
            if(b_reg == (NUM_SAMPLES - 1)) begin
                if(current_state == idle_st)
                    brk <= 12'hFFF;
                else
                    brk <= {brk[10:0], rx};
            end
    end

    assign      dout            =   data_reg >> (9-data_size);
    assign      parity_error    =   p_error_reg & rx_done;
    assign      frame_error     =   f_error_reg & rx_done;
    assign      break_flag      =   (brk == 0);
    assign      match_flag      =   (match_data == dout) & rx_done;

endmodule

/*
    UART Transmitter
*/
module UART_TX #(parameter NUM_SAMPLES = 16, MDW = 8)(
    input   wire                clk,
    input   wire                resetn,
    input   wire                tx_start,        
    input   wire                b_tick,             //baud rate tick
    input   wire [3:0]          data_size,          // 5 - 9
    input   wire                stop_bits_count,    // 0: 1, 1: 2
    input   wire [2:0]          parity_type,        // 000: None, 001: odd, 010: even, 
                                                    // 100: Sticky 0, 101: Sticky 1
    input   wire [MDW-1:0]      d_in,               // input data to transmit
    output  reg                 tx_done,            // Transfer finished
    output  wire                tx                  // output data to RS-232
);
  
    //STATE DEFINES  
    localparam [2:0] idle_st    = 3'b000;
    localparam [2:0] start_st   = 3'b001;
    localparam [2:0] data_st    = 3'b010;
    localparam [2:0] parity_st  = 3'b011;
    localparam [2:0] stop0_st   = 3'b100;
    localparam [2:0] stop1_st   = 3'b101;
/*
    //STATE DEFINES  
    localparam [1:0] idle_st = 2'b00;
    localparam [1:0] start_st = 2'b01;
    localparam [1:0] data_st = 2'b11;
    localparam [1:0] stop_st = 2'b10;
*/
    //Internal Signals  
    reg [2:0]   current_state;
    reg [2:0]   next_state;
    reg [3:0]   b_reg;          // baud tick counter
    reg [3:0]   b_next;
    reg [3:0]   count_reg;      // data bit counter
    reg [3:0]   count_next;
    reg [8:0]   data_reg;       // data register
    reg [8:0]   data_next;
    reg         tx_reg;         // output data reg
    reg         tx_next;

    // prepare the data to claculate the parity by removing any extra bits entered
	// by the user by error
    wire [MDW-1:0] pdata = (d_in) & ~({MDW{1'b1}} << data_size);

    //State Machine  
    always @(posedge clk, negedge resetn) begin
        if(!resetn) begin
            current_state   <= idle_st;
            b_reg           <= 0;
            count_reg       <= 0;
            data_reg        <= 0;
            tx_reg          <= 1'b1;
        end else begin
            current_state   <= next_state;
            b_reg           <= b_next;
            count_reg       <= count_next;
            data_reg        <= data_next;
            tx_reg          <= tx_next;
        end
    end

    //Next State Logic  
    always @* begin
        next_state  =   current_state;
        tx_done     =   1'b0;
        b_next      =   b_reg;
        count_next  =   count_reg;
        data_next   =   data_reg;
        tx_next     =   tx_reg;
        
        case(current_state)
            idle_st: begin
                tx_next = 1'b1;
                if(tx_start) begin
                    next_state = start_st;
                    b_next = 0;
                    data_next = d_in;
                end
            end
            
            start_st: begin //send start bit
                tx_next = 1'b0;
                if(b_tick)
                    if(b_reg == (NUM_SAMPLES)) begin
                        next_state = data_st;
                        b_next = 0;
                        count_next = 0;
                    end
                    else
                        b_next = b_reg + 1;
            end
            
            data_st: begin //send data serially
                tx_next = data_reg[0];
                if(b_tick)
                    if(b_reg == (NUM_SAMPLES - 1)) begin
                        b_next = 0;
                        data_next = data_reg >> 1;
                        if(count_next == (data_size - 1)) 
                            if(parity_type == 3'b000)         
                                next_state = stop0_st;
                            else
                                next_state = parity_st;
                        else
                            count_next = count_reg + 1;
                    end
                    else
                        b_next = b_reg + 1;
            end
            
            parity_st: begin
                tx_next = 1'b0;
                case (parity_type)
                    3'b001 : // Odd parity
                        tx_next = ~^pdata;
                    3'b010 : // Even parity
                        tx_next = ^pdata;
                    3'b100 : // Sticky 0 parity
                        tx_next = 0;
                    3'b101 : // Sticky 1 parity
                        tx_next = 1;
                endcase
                if(b_tick)
                    if(b_reg == (NUM_SAMPLES - 1)) begin
                        b_next = 0;
                        next_state = stop0_st;
                    end else
                        b_next = b_reg + 1;
            end

            stop0_st: begin //send stop bit
                tx_next = 1'b1;
                if(b_tick)
                    if(b_reg == (NUM_SAMPLES - 1)) begin
                        b_next = 0;
                        if(stop_bits_count)         //Two stop bits
                                next_state = stop1_st;
                        else begin                  //One stop bit 
                            next_state = idle_st;
                            tx_done = 1'b1;
                        end        
                    end
                    else
                        b_next = b_reg + 1;
            end

            stop1_st: begin
                tx_next = 1'b1;
                if(b_tick)
                    if(b_reg == (NUM_SAMPLES - 1)) begin //Two stop bits
                        b_next = 0;
                        next_state = idle_st;
                        tx_done = 1'b1;
                    end else
                        b_next = b_reg + 1;
            end
        endcase
    end
  
    assign tx = tx_reg;
  
endmodule

// From efabless/EF_UART hdl/rtl/bus_wrappers/EF_UART_APB.v (commit 793ec5a)
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

`timescale 1ns / 1ps
`default_nettype none

module EF_UART_APB #(
    parameter SC = 8,
    MDW = 9,
    GFLEN = 8,
    FAW = 4
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
    input  wire [1-1:0] rx,
    output wire [1-1:0] tx
);

  localparam RXDATA_REG_OFFSET = 16'h0000;
  localparam TXDATA_REG_OFFSET = 16'h0004;
  localparam PR_REG_OFFSET = 16'h0008;
  localparam CTRL_REG_OFFSET = 16'h000C;
  localparam CFG_REG_OFFSET = 16'h0010;
  localparam MATCH_REG_OFFSET = 16'h001C;
  localparam RX_FIFO_LEVEL_REG_OFFSET = 16'hFE00;
  localparam RX_FIFO_THRESHOLD_REG_OFFSET = 16'hFE04;
  localparam RX_FIFO_FLUSH_REG_OFFSET = 16'hFE08;
  localparam TX_FIFO_LEVEL_REG_OFFSET = 16'hFE10;
  localparam TX_FIFO_THRESHOLD_REG_OFFSET = 16'hFE14;
  localparam TX_FIFO_FLUSH_REG_OFFSET = 16'hFE18;
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

  wire [ 16-1:0] prescaler;
  wire [  1-1:0] en;
  wire [  1-1:0] tx_en;
  wire [  1-1:0] rx_en;
  wire [MDW-1:0] wdata;
  wire [  6-1:0] timeout_bits;
  wire [  1-1:0] loopback_en;
  wire [  1-1:0] glitch_filter_en;
  wire [FAW-1:0] tx_level;
  wire [FAW-1:0] rx_level;
  wire [  1-1:0] rd;
  wire [  1-1:0] wr;
  wire [  1-1:0] tx_fifo_flush;
  wire [  1-1:0] rx_fifo_flush;
  wire [  4-1:0] data_size;
  wire [  1-1:0] stop_bits_count;
  wire [  3-1:0] parity_type;
  wire [FAW-1:0] txfifotr;
  wire [FAW-1:0] rxfifotr;
  wire [MDW-1:0] match_data;
  wire [  1-1:0] tx_empty;
  wire [  1-1:0] tx_full;
  wire [  1-1:0] tx_level_below;
  wire [MDW-1:0] rdata;
  wire [  1-1:0] rx_empty;
  wire [  1-1:0] rx_full;
  wire [  1-1:0] rx_level_above;
  wire [  1-1:0] break_flag;
  wire [  1-1:0] match_flag;
  wire [  1-1:0] frame_error_flag;
  wire [  1-1:0] parity_error_flag;
  wire [  1-1:0] overrun_flag;
  wire [  1-1:0] timeout_flag;

  // Register Definitions
  wire [MDW-1:0] RXDATA_WIRE;

  reg  [   15:0] PR_REG;
  assign prescaler = PR_REG;
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) PR_REG <= 0;
    else if (apb_we & (PADDR[16-1:0] == PR_REG_OFFSET)) PR_REG <= PWDATA[16-1:0];

  reg [4:0] CTRL_REG;
  assign en = CTRL_REG[0 : 0];
  assign tx_en = CTRL_REG[1 : 1];
  assign rx_en = CTRL_REG[2 : 2];
  assign loopback_en = CTRL_REG[3 : 3];
  assign glitch_filter_en = CTRL_REG[4 : 4];
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) CTRL_REG <= 0;
    else if (apb_we & (PADDR[16-1:0] == CTRL_REG_OFFSET)) CTRL_REG <= PWDATA[5-1:0];

  reg [13:0] CFG_REG;
  assign data_size = CFG_REG[3 : 0];
  assign stop_bits_count = CFG_REG[4 : 4];
  assign parity_type = CFG_REG[7 : 5];
  assign timeout_bits = CFG_REG[13 : 8];
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) CFG_REG <= 'h3F08;
    else if (apb_we & (PADDR[16-1:0] == CFG_REG_OFFSET)) CFG_REG <= PWDATA[14-1:0];

  reg [MDW-1:0] MATCH_REG;
  assign match_data = MATCH_REG;
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) MATCH_REG <= 0;
    else if (apb_we & (PADDR[16-1:0] == MATCH_REG_OFFSET)) MATCH_REG <= PWDATA[MDW-1:0];

  wire [FAW-1:0] RX_FIFO_LEVEL_WIRE;
  assign RX_FIFO_LEVEL_WIRE[(FAW-1) : 0] = rx_level;

  reg [FAW-1:0] RX_FIFO_THRESHOLD_REG;
  assign rxfifotr = RX_FIFO_THRESHOLD_REG[(FAW-1) : 0];
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) RX_FIFO_THRESHOLD_REG <= 0;
    else if (apb_we & (PADDR[16-1:0] == RX_FIFO_THRESHOLD_REG_OFFSET))
      RX_FIFO_THRESHOLD_REG <= PWDATA[FAW-1:0];

  reg [0:0] RX_FIFO_FLUSH_REG;
  assign rx_fifo_flush = RX_FIFO_FLUSH_REG[0 : 0];
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) RX_FIFO_FLUSH_REG <= 0;
    else if (apb_we & (PADDR[16-1:0] == RX_FIFO_FLUSH_REG_OFFSET))
      RX_FIFO_FLUSH_REG <= PWDATA[1-1:0];
    else RX_FIFO_FLUSH_REG <= 1'h0 & RX_FIFO_FLUSH_REG;

  wire [FAW-1:0] TX_FIFO_LEVEL_WIRE;
  assign TX_FIFO_LEVEL_WIRE[(FAW-1) : 0] = tx_level;

  reg [FAW-1:0] TX_FIFO_THRESHOLD_REG;
  assign txfifotr = TX_FIFO_THRESHOLD_REG[(FAW-1) : 0];
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) TX_FIFO_THRESHOLD_REG <= 0;
    else if (apb_we & (PADDR[16-1:0] == TX_FIFO_THRESHOLD_REG_OFFSET))
      TX_FIFO_THRESHOLD_REG <= PWDATA[FAW-1:0];

  reg [0:0] TX_FIFO_FLUSH_REG;
  assign tx_fifo_flush = TX_FIFO_FLUSH_REG[0 : 0];
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) TX_FIFO_FLUSH_REG <= 0;
    else if (apb_we & (PADDR[16-1:0] == TX_FIFO_FLUSH_REG_OFFSET))
      TX_FIFO_FLUSH_REG <= PWDATA[1-1:0];
    else TX_FIFO_FLUSH_REG <= 1'h0 & TX_FIFO_FLUSH_REG;

  localparam GCLK_REG_OFFSET = 16'hFF10;
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) GCLK_REG <= 0;
    else if (apb_we & (PADDR[16-1:0] == GCLK_REG_OFFSET)) GCLK_REG <= PWDATA[1-1:0];

  reg  [   9:0] IM_REG;
  reg  [   9:0] IC_REG;
  reg  [   9:0] RIS_REG;

  wire [10-1:0] MIS_REG = RIS_REG & IM_REG;
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) IM_REG <= 0;
    else if (apb_we & (PADDR[16-1:0] == IM_REG_OFFSET)) IM_REG <= PWDATA[10-1:0];
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) IC_REG <= 10'b0;
    else if (apb_we & (PADDR[16-1:0] == IC_REG_OFFSET)) IC_REG <= PWDATA[10-1:0];
    else IC_REG <= 10'd0;

  wire [0:0] TXE = tx_empty;
  wire [0:0] RXF = rx_full;
  wire [0:0] TXB = tx_level_below;
  wire [0:0] RXA = rx_level_above;
  wire [0:0] BRK = break_flag;
  wire [0:0] MATCH = match_flag;
  wire [0:0] FE = frame_error_flag;
  wire [0:0] PRE = parity_error_flag;
  wire [0:0] OR = overrun_flag;
  wire [0:0] RTO = timeout_flag;

  integer _i_;
  always @(posedge PCLK or negedge PRESETn)
    if (~PRESETn) RIS_REG <= 0;
    else begin
      for (_i_ = 0; _i_ < 1; _i_ = _i_ + 1) begin
        if (IC_REG[_i_]) RIS_REG[_i_] <= 1'b0;
        else if (TXE[_i_-0] == 1'b1) RIS_REG[_i_] <= 1'b1;
      end
      for (_i_ = 1; _i_ < 2; _i_ = _i_ + 1) begin
        if (IC_REG[_i_]) RIS_REG[_i_] <= 1'b0;
        else if (RXF[_i_-1] == 1'b1) RIS_REG[_i_] <= 1'b1;
      end
      for (_i_ = 2; _i_ < 3; _i_ = _i_ + 1) begin
        if (IC_REG[_i_]) RIS_REG[_i_] <= 1'b0;
        else if (TXB[_i_-2] == 1'b1) RIS_REG[_i_] <= 1'b1;
      end
      for (_i_ = 3; _i_ < 4; _i_ = _i_ + 1) begin
        if (IC_REG[_i_]) RIS_REG[_i_] <= 1'b0;
        else if (RXA[_i_-3] == 1'b1) RIS_REG[_i_] <= 1'b1;
      end
      for (_i_ = 4; _i_ < 5; _i_ = _i_ + 1) begin
        if (IC_REG[_i_]) RIS_REG[_i_] <= 1'b0;
        else if (BRK[_i_-4] == 1'b1) RIS_REG[_i_] <= 1'b1;
      end
      for (_i_ = 5; _i_ < 6; _i_ = _i_ + 1) begin
        if (IC_REG[_i_]) RIS_REG[_i_] <= 1'b0;
        else if (MATCH[_i_-5] == 1'b1) RIS_REG[_i_] <= 1'b1;
      end
      for (_i_ = 6; _i_ < 7; _i_ = _i_ + 1) begin
        if (IC_REG[_i_]) RIS_REG[_i_] <= 1'b0;
        else if (FE[_i_-6] == 1'b1) RIS_REG[_i_] <= 1'b1;
      end
      for (_i_ = 7; _i_ < 8; _i_ = _i_ + 1) begin
        if (IC_REG[_i_]) RIS_REG[_i_] <= 1'b0;
        else if (PRE[_i_-7] == 1'b1) RIS_REG[_i_] <= 1'b1;
      end
      for (_i_ = 8; _i_ < 9; _i_ = _i_ + 1) begin
        if (IC_REG[_i_]) RIS_REG[_i_] <= 1'b0;
        else if (OR[_i_-8] == 1'b1) RIS_REG[_i_] <= 1'b1;
      end
      for (_i_ = 9; _i_ < 10; _i_ = _i_ + 1) begin
        if (IC_REG[_i_]) RIS_REG[_i_] <= 1'b0;
        else if (RTO[_i_-9] == 1'b1) RIS_REG[_i_] <= 1'b1;
      end
    end

  assign IRQ = |MIS_REG;

  EF_UART #(
      .SC(SC),
      .MDW(MDW),
      .GFLEN(GFLEN),
      .FAW(FAW)
  ) instance_to_wrap (
      .clk(clk),
      .rst_n(rst_n),
      .prescaler(prescaler),
      .en(en),
      .tx_en(tx_en),
      .rx_en(rx_en),
      .wdata(wdata),
      .timeout_bits(timeout_bits),
      .loopback_en(loopback_en),
      .glitch_filter_en(glitch_filter_en),
      .tx_level(tx_level),
      .rx_level(rx_level),
      .rd(rd),
      .wr(wr),
      .tx_fifo_flush(tx_fifo_flush),
      .rx_fifo_flush(rx_fifo_flush),
      .data_size(data_size),
      .stop_bits_count(stop_bits_count),
      .parity_type(parity_type),
      .txfifotr(txfifotr),
      .rxfifotr(rxfifotr),
      .match_data(match_data),
      .tx_empty(tx_empty),
      .tx_full(tx_full),
      .tx_level_below(tx_level_below),
      .rdata(rdata),
      .rx_empty(rx_empty),
      .rx_full(rx_full),
      .rx_level_above(rx_level_above),
      .break_flag(break_flag),
      .match_flag(match_flag),
      .frame_error_flag(frame_error_flag),
      .parity_error_flag(parity_error_flag),
      .overrun_flag(overrun_flag),
      .timeout_flag(timeout_flag),
      .rx(rx),
      .tx(tx)
  );

  assign	PRDATA = 
			(PADDR[16-1:0] == RXDATA_REG_OFFSET)	? RXDATA_WIRE :
			(PADDR[16-1:0] == PR_REG_OFFSET)	? PR_REG :
			(PADDR[16-1:0] == CTRL_REG_OFFSET)	? CTRL_REG :
			(PADDR[16-1:0] == CFG_REG_OFFSET)	? CFG_REG :
			(PADDR[16-1:0] == MATCH_REG_OFFSET)	? MATCH_REG :
			(PADDR[16-1:0] == RX_FIFO_LEVEL_REG_OFFSET)	? RX_FIFO_LEVEL_WIRE :
			(PADDR[16-1:0] == RX_FIFO_THRESHOLD_REG_OFFSET)	? RX_FIFO_THRESHOLD_REG :
			(PADDR[16-1:0] == RX_FIFO_FLUSH_REG_OFFSET)	? RX_FIFO_FLUSH_REG :
			(PADDR[16-1:0] == TX_FIFO_LEVEL_REG_OFFSET)	? TX_FIFO_LEVEL_WIRE :
			(PADDR[16-1:0] == TX_FIFO_THRESHOLD_REG_OFFSET)	? TX_FIFO_THRESHOLD_REG :
			(PADDR[16-1:0] == TX_FIFO_FLUSH_REG_OFFSET)	? TX_FIFO_FLUSH_REG :
			(PADDR[16-1:0] == IM_REG_OFFSET)	? IM_REG :
			(PADDR[16-1:0] == MIS_REG_OFFSET)	? MIS_REG :
			(PADDR[16-1:0] == RIS_REG_OFFSET)	? RIS_REG :
			(PADDR[16-1:0] == GCLK_REG_OFFSET)	? GCLK_REG :
			32'hDEADBEEF;

  assign PREADY = 1'b1;

  assign RXDATA_WIRE = rdata;
  assign rd = (apb_re & (PADDR[16-1:0] == RXDATA_REG_OFFSET));
  assign wdata = PWDATA;
  assign wr = (apb_we & (PADDR[16-1:0] == TXDATA_REG_OFFSET));
endmodule

