// rtl-env seed oc_i2c: the OpenCores I2C master (Richard Herveille, OpenCores SVN rev 76)
// with an in-house I2C target and bus. The three OpenCores files are unchanged except that
// `include lines were replaced by the inlined defines below and #1 simulation delays were removed.
`timescale 1ns / 10ps

/////////////////////////////////////////////////////////////////////
////                                                             ////
////  WISHBONE rev.B2 compliant I2C Master controller defines    ////
////                                                             ////
////                                                             ////
////  Author: Richard Herveille                                  ////
////          richard@asics.ws                                   ////
////          www.asics.ws                                       ////
////                                                             ////
////  Downloaded from: http://www.opencores.org/projects/i2c/    ////
////                                                             ////
/////////////////////////////////////////////////////////////////////
////                                                             ////
//// Copyright (C) 2001 Richard Herveille                        ////
////                    richard@asics.ws                         ////
////                                                             ////
//// This source file may be used and distributed without        ////
//// restriction provided that this copyright statement is not   ////
//// removed from the file and that any derivative work contains ////
//// the original copyright notice and the associated disclaimer.////
////                                                             ////
////     THIS SOFTWARE IS PROVIDED ``AS IS'' AND WITHOUT ANY     ////
//// EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED   ////
//// TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS   ////
//// FOR A PARTICULAR PURPOSE. IN NO EVENT SHALL THE AUTHOR      ////
//// OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,         ////
//// INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES    ////
//// (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE   ////
//// GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR        ////
//// BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF  ////
//// LIABILITY, WHETHER IN  CONTRACT, STRICT LIABILITY, OR TORT  ////
//// (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT  ////
//// OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE         ////
//// POSSIBILITY OF SUCH DAMAGE.                                 ////
////                                                             ////
/////////////////////////////////////////////////////////////////////

//  CVS Log
//
//  $Id: i2c_master_defines.v,v 1.3 2001-11-05 11:59:25 rherveille Exp $
//
//  $Date: 2001-11-05 11:59:25 $
//  $Revision: 1.3 $
//  $Author: rherveille $
//  $Locker:  $
//  $State: Exp $
//
// Change History:
//               $Log: not supported by cvs2svn $


// I2C registers wishbone addresses

// bitcontroller states
`define I2C_CMD_NOP   4'b0000
`define I2C_CMD_START 4'b0001
`define I2C_CMD_STOP  4'b0010
`define I2C_CMD_WRITE 4'b0100
`define I2C_CMD_READ  4'b1000

/////////////////////////////////////////////////////////////////////
////                                                             ////
////  WISHBONE rev.B2 compliant I2C Master bit-controller        ////
////                                                             ////
////                                                             ////
////  Author: Richard Herveille                                  ////
////          richard@asics.ws                                   ////
////          www.asics.ws                                       ////
////                                                             ////
////  Downloaded from: http://www.opencores.org/projects/i2c/    ////
////                                                             ////
/////////////////////////////////////////////////////////////////////
////                                                             ////
//// Copyright (C) 2001 Richard Herveille                        ////
////                    richard@asics.ws                         ////
////                                                             ////
//// This source file may be used and distributed without        ////
//// restriction provided that this copyright statement is not   ////
//// removed from the file and that any derivative work contains ////
//// the original copyright notice and the associated disclaimer.////
////                                                             ////
////     THIS SOFTWARE IS PROVIDED ``AS IS'' AND WITHOUT ANY     ////
//// EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED   ////
//// TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS   ////
//// FOR A PARTICULAR PURPOSE. IN NO EVENT SHALL THE AUTHOR      ////
//// OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,         ////
//// INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES    ////
//// (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE   ////
//// GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR        ////
//// BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF  ////
//// LIABILITY, WHETHER IN  CONTRACT, STRICT LIABILITY, OR TORT  ////
//// (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT  ////
//// OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE         ////
//// POSSIBILITY OF SUCH DAMAGE.                                 ////
////                                                             ////
/////////////////////////////////////////////////////////////////////

//  CVS Log
//
//  $Id: i2c_master_bit_ctrl.v,v 1.14 2009-01-20 10:25:29 rherveille Exp $
//
//  $Date: 2009-01-20 10:25:29 $
//  $Revision: 1.14 $
//  $Author: rherveille $
//  $Locker:  $
//  $State: Exp $
//
// Change History:
//               $Log: $
//               Revision 1.14  2009/01/20 10:25:29  rherveille
//               Added clock synchronization logic
//               Fixed slave_wait signal
//
//               Revision 1.13  2009/01/19 20:29:26  rherveille
//               Fixed synopsys miss spell (synopsis)
//               Fixed cr[0] register width
//               Fixed ! usage instead of ~
//               Fixed bit controller parameter width to 18bits
//
//               Revision 1.12  2006/09/04 09:08:13  rherveille
//               fixed short scl high pulse after clock stretch
//               fixed slave model not returning correct '(n)ack' signal
//
//               Revision 1.11  2004/05/07 11:02:26  rherveille
//               Fixed a bug where the core would signal an arbitration lost (AL bit set), when another master controls the bus and the other master generates a STOP bit.
//
//               Revision 1.10  2003/08/09 07:01:33  rherveille
//               Fixed a bug in the Arbitration Lost generation caused by delay on the (external) sda line.
//               Fixed a potential bug in the byte controller's host-acknowledge generation.
//
//               Revision 1.9  2003/03/10 14:26:37  rherveille
//               Fixed cmd_ack generation item (no bug).
//
//               Revision 1.8  2003/02/05 00:06:10  rherveille
//               Fixed a bug where the core would trigger an erroneous 'arbitration lost' interrupt after being reset, when the reset pulse width < 3 clk cycles.
//
//               Revision 1.7  2002/12/26 16:05:12  rherveille
//               Small code simplifications
//
//               Revision 1.6  2002/12/26 15:02:32  rherveille
//               Core is now a Multimaster I2C controller
//
//               Revision 1.5  2002/11/30 22:24:40  rherveille
//               Cleaned up code
//
//               Revision 1.4  2002/10/30 18:10:07  rherveille
//               Fixed some reported minor start/stop generation timing issuess.
//
//               Revision 1.3  2002/06/15 07:37:03  rherveille
//               Fixed a small timing bug in the bit controller.\nAdded verilog simulation environment.
//
//               Revision 1.2  2001/11/05 11:59:25  rherveille
//               Fixed wb_ack_o generation bug.
//               Fixed bug in the byte_controller statemachine.
//               Added headers.
//

//
/////////////////////////////////////
// Bit controller section
/////////////////////////////////////
//
// Translate simple commands into SCL/SDA transitions
// Each command has 5 states, A/B/C/D/idle
//
// start:	SCL	~~~~~~~~~~\____
//	SDA	~~~~~~~~\______
//		 x | A | B | C | D | i
//
// repstart	SCL	____/~~~~\___
//	SDA	__/~~~\______
//		 x | A | B | C | D | i
//
// stop	SCL	____/~~~~~~~~
//	SDA	==\____/~~~~~
//		 x | A | B | C | D | i
//
//- write	SCL	____/~~~~\____
//	SDA	==X=========X=
//		 x | A | B | C | D | i
//
//- read	SCL	____/~~~~\____
//	SDA	XXXX=====XXXX
//		 x | A | B | C | D | i
//

// Timing:     Normal mode      Fast mode
///////////////////////////////////////////////////////////////////////
// Fscl        100KHz           400KHz
// Th_scl      4.0us            0.6us   High period of SCL
// Tl_scl      4.7us            1.3us   Low period of SCL
// Tsu:sta     4.7us            0.6us   setup time for a repeated start condition
// Tsu:sto     4.0us            0.6us   setup time for a stop conditon
// Tbuf        4.7us            1.3us   Bus free time between a stop and start condition
//

// synopsys translate_off
// synopsys translate_on
module i2c_master_bit_ctrl (
    input             clk,      // system clock
    input             rst,      // synchronous active high reset
    input             nReset,   // asynchronous active low reset
    input             ena,      // core enable signal

    input      [15:0] clk_cnt,  // clock prescale value

    input      [ 3:0] cmd,      // command (from byte controller)
    output reg        cmd_ack,  // command complete acknowledge
    output reg        busy,     // i2c bus busy
    output reg        al,       // i2c bus arbitration lost

    input             din,
    output reg        dout,

    input             scl_i,    // i2c clock line input
    output            scl_o,    // i2c clock line output
    output reg        scl_oen,  // i2c clock line output enable (active low)
    input             sda_i,    // i2c data line input
    output            sda_o,    // i2c data line output
    output reg        sda_oen   // i2c data line output enable (active low)
);


    //
    // variable declarations
    //

    reg [ 1:0] cSCL, cSDA;      // capture SCL and SDA
    reg [ 2:0] fSCL, fSDA;      // SCL and SDA filter inputs
    reg        sSCL, sSDA;      // filtered and synchronized SCL and SDA inputs
    reg        dSCL, dSDA;      // delayed versions of sSCL and sSDA
    reg        dscl_oen;        // delayed scl_oen
    reg        sda_chk;         // check SDA output (Multi-master arbitration)
    reg        clk_en;          // clock generation signals
    reg        slave_wait;      // slave inserts wait states
    reg [15:0] cnt;             // clock divider counter (synthesis)
    reg [13:0] filter_cnt;      // clock divider for filter


    // state machine variable
    reg [17:0] c_state; // synopsys enum_state

    //
    // module body
    //

    // whenever the slave is not ready it can delay the cycle by pulling SCL low
    // delay scl_oen
    always @(posedge clk)
      dscl_oen <= scl_oen;

    // slave_wait is asserted when master wants to drive SCL high, but the slave pulls it low
    // slave_wait remains asserted until the slave releases SCL
    always @(posedge clk or negedge nReset)
      if (!nReset) slave_wait <= 1'b0;
      else         slave_wait <= (scl_oen & ~dscl_oen & ~sSCL) | (slave_wait & ~sSCL);

    // master drives SCL high, but another master pulls it low
    // master start counting down its low cycle now (clock synchronization)
    wire scl_sync   = dSCL & ~sSCL & scl_oen;


    // generate clk enable signal
    always @(posedge clk or negedge nReset)
      if (~nReset)
      begin
          cnt    <= 16'h0;
          clk_en <= 1'b1;
      end
      else if (rst || ~|cnt || !ena || scl_sync)
      begin
          cnt    <= clk_cnt;
          clk_en <= 1'b1;
      end
      else if (slave_wait)
      begin
          cnt    <= cnt;
          clk_en <= 1'b0;    
      end
      else
      begin
          cnt    <= cnt - 16'h1;
          clk_en <= 1'b0;
      end


    // generate bus status controller

    // capture SDA and SCL
    // reduce metastability risk
    always @(posedge clk or negedge nReset)
      if (!nReset)
      begin
          cSCL <= 2'b00;
          cSDA <= 2'b00;
      end
      else if (rst)
      begin
          cSCL <= 2'b00;
          cSDA <= 2'b00;
      end
      else
      begin
          cSCL <= {cSCL[0],scl_i};
          cSDA <= {cSDA[0],sda_i};
      end


    // filter SCL and SDA signals; (attempt to) remove glitches
    always @(posedge clk or negedge nReset)
      if      (!nReset     ) filter_cnt <= 14'h0;
      else if (rst || !ena ) filter_cnt <= 14'h0;
      else if (~|filter_cnt) filter_cnt <= clk_cnt >> 2; //16x I2C bus frequency
      else                   filter_cnt <= filter_cnt -1;


    always @(posedge clk or negedge nReset)
      if (!nReset)
      begin
          fSCL <= 3'b111;
          fSDA <= 3'b111;
      end
      else if (rst)
      begin
          fSCL <= 3'b111;
          fSDA <= 3'b111;
      end
      else if (~|filter_cnt)
      begin
          fSCL <= {fSCL[1:0],cSCL[1]};
          fSDA <= {fSDA[1:0],cSDA[1]};
      end


    // generate filtered SCL and SDA signals
    always @(posedge clk or negedge nReset)
      if (~nReset)
      begin
          sSCL <= 1'b1;
          sSDA <= 1'b1;

          dSCL <= 1'b1;
          dSDA <= 1'b1;
      end
      else if (rst)
      begin
          sSCL <= 1'b1;
          sSDA <= 1'b1;

          dSCL <= 1'b1;
          dSDA <= 1'b1;
      end
      else
      begin
          sSCL <= &fSCL[2:1] | &fSCL[1:0] | (fSCL[2] & fSCL[0]);
          sSDA <= &fSDA[2:1] | &fSDA[1:0] | (fSDA[2] & fSDA[0]);

          dSCL <= sSCL;
          dSDA <= sSDA;
      end

    // detect start condition => detect falling edge on SDA while SCL is high
    // detect stop condition => detect rising edge on SDA while SCL is high
    reg sta_condition;
    reg sto_condition;
    always @(posedge clk or negedge nReset)
      if (~nReset)
      begin
          sta_condition <= 1'b0;
          sto_condition <= 1'b0;
      end
      else if (rst)
      begin
          sta_condition <= 1'b0;
          sto_condition <= 1'b0;
      end
      else
      begin
          sta_condition <= ~sSDA &  dSDA & sSCL;
          sto_condition <= sSDA & ~dSDA & sSCL;
      end


    // generate i2c bus busy signal
    always @(posedge clk or negedge nReset)
      if      (!nReset) busy <= 1'b0;
      else if (rst    ) busy <= 1'b0;
      else              busy <= (sta_condition | busy) & ~sto_condition;


    // generate arbitration lost signal
    // aribitration lost when:
    // 1) master drives SDA high, but the i2c bus is low
    // 2) stop detected while not requested
    reg cmd_stop;
    always @(posedge clk or negedge nReset)
      if (~nReset)
          cmd_stop <= 1'b0;
      else if (rst)
          cmd_stop <= 1'b0;
      else if (clk_en)
          cmd_stop <= cmd == `I2C_CMD_STOP;

    always @(posedge clk or negedge nReset)
      if (~nReset)
          al <= 1'b0;
      else if (rst)
          al <= 1'b0;
      else
          al <= (sda_chk & ~sSDA & sda_oen) | (|c_state & sto_condition & ~cmd_stop);


    // generate dout signal (store SDA on rising edge of SCL)
    always @(posedge clk)
      if (sSCL & ~dSCL) dout <= sSDA;


    // generate statemachine

    // nxt_state decoder
    parameter [17:0] idle    = 18'b0_0000_0000_0000_0000;
    parameter [17:0] start_a = 18'b0_0000_0000_0000_0001;
    parameter [17:0] start_b = 18'b0_0000_0000_0000_0010;
    parameter [17:0] start_c = 18'b0_0000_0000_0000_0100;
    parameter [17:0] start_d = 18'b0_0000_0000_0000_1000;
    parameter [17:0] start_e = 18'b0_0000_0000_0001_0000;
    parameter [17:0] stop_a  = 18'b0_0000_0000_0010_0000;
    parameter [17:0] stop_b  = 18'b0_0000_0000_0100_0000;
    parameter [17:0] stop_c  = 18'b0_0000_0000_1000_0000;
    parameter [17:0] stop_d  = 18'b0_0000_0001_0000_0000;
    parameter [17:0] rd_a    = 18'b0_0000_0010_0000_0000;
    parameter [17:0] rd_b    = 18'b0_0000_0100_0000_0000;
    parameter [17:0] rd_c    = 18'b0_0000_1000_0000_0000;
    parameter [17:0] rd_d    = 18'b0_0001_0000_0000_0000;
    parameter [17:0] wr_a    = 18'b0_0010_0000_0000_0000;
    parameter [17:0] wr_b    = 18'b0_0100_0000_0000_0000;
    parameter [17:0] wr_c    = 18'b0_1000_0000_0000_0000;
    parameter [17:0] wr_d    = 18'b1_0000_0000_0000_0000;

    always @(posedge clk or negedge nReset)
      if (!nReset)
      begin
          c_state <= idle;
          cmd_ack <= 1'b0;
          scl_oen <= 1'b1;
          sda_oen <= 1'b1;
          sda_chk <= 1'b0;
      end
      else if (rst | al)
      begin
          c_state <= idle;
          cmd_ack <= 1'b0;
          scl_oen <= 1'b1;
          sda_oen <= 1'b1;
          sda_chk <= 1'b0;
      end
      else
      begin
          cmd_ack   <= 1'b0; // default no command acknowledge + assert cmd_ack only 1clk cycle

          if (clk_en)
              case (c_state) // synopsys full_case parallel_case
                    // idle state
                    idle:
                    begin
                        case (cmd) // synopsys full_case parallel_case
                             `I2C_CMD_START: c_state <= start_a;
                             `I2C_CMD_STOP:  c_state <= stop_a;
                             `I2C_CMD_WRITE: c_state <= wr_a;
                             `I2C_CMD_READ:  c_state <= rd_a;
                             default:        c_state <= idle;
                        endcase

                        scl_oen <= scl_oen; // keep SCL in same state
                        sda_oen <= sda_oen; // keep SDA in same state
                        sda_chk <= 1'b0;    // don't check SDA output
                    end

                    // start
                    start_a:
                    begin
                        c_state <= start_b;
                        scl_oen <= scl_oen; // keep SCL in same state
                        sda_oen <= 1'b1;    // set SDA high
                        sda_chk <= 1'b0;    // don't check SDA output
                    end

                    start_b:
                    begin
                        c_state <= start_c;
                        scl_oen <= 1'b1; // set SCL high
                        sda_oen <= 1'b1; // keep SDA high
                        sda_chk <= 1'b0; // don't check SDA output
                    end

                    start_c:
                    begin
                        c_state <= start_d;
                        scl_oen <= 1'b1; // keep SCL high
                        sda_oen <= 1'b0; // set SDA low
                        sda_chk <= 1'b0; // don't check SDA output
                    end

                    start_d:
                    begin
                        c_state <= start_e;
                        scl_oen <= 1'b1; // keep SCL high
                        sda_oen <= 1'b0; // keep SDA low
                        sda_chk <= 1'b0; // don't check SDA output
                    end

                    start_e:
                    begin
                        c_state <= idle;
                        cmd_ack <= 1'b1;
                        scl_oen <= 1'b0; // set SCL low
                        sda_oen <= 1'b0; // keep SDA low
                        sda_chk <= 1'b0; // don't check SDA output
                    end

                    // stop
                    stop_a:
                    begin
                        c_state <= stop_b;
                        scl_oen <= 1'b0; // keep SCL low
                        sda_oen <= 1'b0; // set SDA low
                        sda_chk <= 1'b0; // don't check SDA output
                    end

                    stop_b:
                    begin
                        c_state <= stop_c;
                        scl_oen <= 1'b1; // set SCL high
                        sda_oen <= 1'b0; // keep SDA low
                        sda_chk <= 1'b0; // don't check SDA output
                    end

                    stop_c:
                    begin
                        c_state <= stop_d;
                        scl_oen <= 1'b1; // keep SCL high
                        sda_oen <= 1'b0; // keep SDA low
                        sda_chk <= 1'b0; // don't check SDA output
                    end

                    stop_d:
                    begin
                        c_state <= idle;
                        cmd_ack <= 1'b1;
                        scl_oen <= 1'b1; // keep SCL high
                        sda_oen <= 1'b1; // set SDA high
                        sda_chk <= 1'b0; // don't check SDA output
                    end

                    // read
                    rd_a:
                    begin
                        c_state <= rd_b;
                        scl_oen <= 1'b0; // keep SCL low
                        sda_oen <= 1'b1; // tri-state SDA
                        sda_chk <= 1'b0; // don't check SDA output
                    end

                    rd_b:
                    begin
                        c_state <= rd_c;
                        scl_oen <= 1'b1; // set SCL high
                        sda_oen <= 1'b1; // keep SDA tri-stated
                        sda_chk <= 1'b0; // don't check SDA output
                    end

                    rd_c:
                    begin
                        c_state <= rd_d;
                        scl_oen <= 1'b1; // keep SCL high
                        sda_oen <= 1'b1; // keep SDA tri-stated
                        sda_chk <= 1'b0; // don't check SDA output
                    end

                    rd_d:
                    begin
                        c_state <= idle;
                        cmd_ack <= 1'b1;
                        scl_oen <= 1'b0; // set SCL low
                        sda_oen <= 1'b1; // keep SDA tri-stated
                        sda_chk <= 1'b0; // don't check SDA output
                    end

                    // write
                    wr_a:
                    begin
                        c_state <= wr_b;
                        scl_oen <= 1'b0; // keep SCL low
                        sda_oen <= din;  // set SDA
                        sda_chk <= 1'b0; // don't check SDA output (SCL low)
                    end

                    wr_b:
                    begin
                        c_state <= wr_c;
                        scl_oen <= 1'b1; // set SCL high
                        sda_oen <= din;  // keep SDA
                        sda_chk <= 1'b0; // don't check SDA output yet
                                            // allow some time for SDA and SCL to settle
                    end

                    wr_c:
                    begin
                        c_state <= wr_d;
                        scl_oen <= 1'b1; // keep SCL high
                        sda_oen <= din;
                        sda_chk <= 1'b1; // check SDA output
                    end

                    wr_d:
                    begin
                        c_state <= idle;
                        cmd_ack <= 1'b1;
                        scl_oen <= 1'b0; // set SCL low
                        sda_oen <= din;
                        sda_chk <= 1'b0; // don't check SDA output (SCL low)
                    end

              endcase
      end


    // assign scl and sda output (always gnd)
    assign scl_o = 1'b0;
    assign sda_o = 1'b0;

endmodule

/////////////////////////////////////////////////////////////////////
////                                                             ////
////  WISHBONE rev.B2 compliant I2C Master byte-controller       ////
////                                                             ////
////                                                             ////
////  Author: Richard Herveille                                  ////
////          richard@asics.ws                                   ////
////          www.asics.ws                                       ////
////                                                             ////
////  Downloaded from: http://www.opencores.org/projects/i2c/    ////
////                                                             ////
/////////////////////////////////////////////////////////////////////
////                                                             ////
//// Copyright (C) 2001 Richard Herveille                        ////
////                    richard@asics.ws                         ////
////                                                             ////
//// This source file may be used and distributed without        ////
//// restriction provided that this copyright statement is not   ////
//// removed from the file and that any derivative work contains ////
//// the original copyright notice and the associated disclaimer.////
////                                                             ////
////     THIS SOFTWARE IS PROVIDED ``AS IS'' AND WITHOUT ANY     ////
//// EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED   ////
//// TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS   ////
//// FOR A PARTICULAR PURPOSE. IN NO EVENT SHALL THE AUTHOR      ////
//// OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,         ////
//// INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES    ////
//// (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE   ////
//// GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR        ////
//// BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF  ////
//// LIABILITY, WHETHER IN  CONTRACT, STRICT LIABILITY, OR TORT  ////
//// (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT  ////
//// OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE         ////
//// POSSIBILITY OF SUCH DAMAGE.                                 ////
////                                                             ////
/////////////////////////////////////////////////////////////////////

//  CVS Log
//
//  $Id: i2c_master_byte_ctrl.v,v 1.8 2009-01-19 20:29:26 rherveille Exp $
//
//  $Date: 2009-01-19 20:29:26 $
//  $Revision: 1.8 $
//  $Author: rherveille $
//  $Locker:  $
//  $State: Exp $
//
// Change History:
//               $Log: not supported by cvs2svn $
//               Revision 1.7  2004/02/18 11:40:46  rherveille
//               Fixed a potential bug in the statemachine. During a 'stop' 2 cmd_ack signals were generated. Possibly canceling a new start command.
//
//               Revision 1.6  2003/08/09 07:01:33  rherveille
//               Fixed a bug in the Arbitration Lost generation caused by delay on the (external) sda line.
//               Fixed a potential bug in the byte controller's host-acknowledge generation.
//
//               Revision 1.5  2002/12/26 15:02:32  rherveille
//               Core is now a Multimaster I2C controller
//
//               Revision 1.4  2002/11/30 22:24:40  rherveille
//               Cleaned up code
//
//               Revision 1.3  2001/11/05 11:59:25  rherveille
//               Fixed wb_ack_o generation bug.
//               Fixed bug in the byte_controller statemachine.
//               Added headers.
//

// synopsys translate_off
// synopsys translate_on
module i2c_master_byte_ctrl (
	clk, rst, nReset, ena, clk_cnt, start, stop, read, write, ack_in, din,
	cmd_ack, ack_out, dout, i2c_busy, i2c_al, scl_i, scl_o, scl_oen, sda_i, sda_o, sda_oen );

	//
	// inputs & outputs
	//
	input clk;     // master clock
	input rst;     // synchronous active high reset
	input nReset;  // asynchronous active low reset
	input ena;     // core enable signal

	input [15:0] clk_cnt; // 4x SCL

	// control inputs
	input       start;
	input       stop;
	input       read;
	input       write;
	input       ack_in;
	input [7:0] din;

	// status outputs
	output       cmd_ack;
	reg cmd_ack;
	output       ack_out;
	reg ack_out;
	output       i2c_busy;
	output       i2c_al;
	output [7:0] dout;

	// I2C signals
	input  scl_i;
	output scl_o;
	output scl_oen;
	input  sda_i;
	output sda_o;
	output sda_oen;


	//
	// Variable declarations
	//

	// statemachine
	parameter [4:0] ST_IDLE  = 5'b0_0000;
	parameter [4:0] ST_START = 5'b0_0001;
	parameter [4:0] ST_READ  = 5'b0_0010;
	parameter [4:0] ST_WRITE = 5'b0_0100;
	parameter [4:0] ST_ACK   = 5'b0_1000;
	parameter [4:0] ST_STOP  = 5'b1_0000;

	// signals for bit_controller
	reg  [3:0] core_cmd;
	reg        core_txd;
	wire       core_ack, core_rxd;

	// signals for shift register
	reg [7:0] sr; //8bit shift register
	reg       shift, ld;

	// signals for state machine
	wire       go;
	reg  [2:0] dcnt;
	wire       cnt_done;

	//
	// Module body
	//

	// hookup bit_controller
	i2c_master_bit_ctrl bit_controller (
		.clk     ( clk      ),
		.rst     ( rst      ),
		.nReset  ( nReset   ),
		.ena     ( ena      ),
		.clk_cnt ( clk_cnt  ),
		.cmd     ( core_cmd ),
		.cmd_ack ( core_ack ),
		.busy    ( i2c_busy ),
		.al      ( i2c_al   ),
		.din     ( core_txd ),
		.dout    ( core_rxd ),
		.scl_i   ( scl_i    ),
		.scl_o   ( scl_o    ),
		.scl_oen ( scl_oen  ),
		.sda_i   ( sda_i    ),
		.sda_o   ( sda_o    ),
		.sda_oen ( sda_oen  )
	);

	// generate go-signal
	assign go = (read | write | stop) & ~cmd_ack;

	// assign dout output to shift-register
	assign dout = sr;

	// generate shift register
	always @(posedge clk or negedge nReset)
	  if (!nReset)
	    sr <= 8'h0;
	  else if (rst)
	    sr <= 8'h0;
	  else if (ld)
	    sr <= din;
	  else if (shift)
	    sr <= {sr[6:0], core_rxd};

	// generate counter
	always @(posedge clk or negedge nReset)
	  if (!nReset)
	    dcnt <= 3'h0;
	  else if (rst)
	    dcnt <= 3'h0;
	  else if (ld)
	    dcnt <= 3'h7;
	  else if (shift)
	    dcnt <= dcnt - 3'h1;

	assign cnt_done = ~(|dcnt);

	//
	// state machine
	//
	reg [4:0] c_state; // synopsys enum_state

	always @(posedge clk or negedge nReset)
	  if (!nReset)
	    begin
	        core_cmd <= `I2C_CMD_NOP;
	        core_txd <= 1'b0;
	        shift    <= 1'b0;
	        ld       <= 1'b0;
	        cmd_ack  <= 1'b0;
	        c_state  <= ST_IDLE;
	        ack_out  <= 1'b0;
	    end
	  else if (rst | i2c_al)
	   begin
	       core_cmd <= `I2C_CMD_NOP;
	       core_txd <= 1'b0;
	       shift    <= 1'b0;
	       ld       <= 1'b0;
	       cmd_ack  <= 1'b0;
	       c_state  <= ST_IDLE;
	       ack_out  <= 1'b0;
	   end
	else
	  begin
	      // initially reset all signals
	      core_txd <= sr[7];
	      shift    <= 1'b0;
	      ld       <= 1'b0;
	      cmd_ack  <= 1'b0;

	      case (c_state) // synopsys full_case parallel_case
	        ST_IDLE:
	          if (go)
	            begin
	                if (start)
	                  begin
	                      c_state  <= ST_START;
	                      core_cmd <= `I2C_CMD_START;
	                  end
	                else if (read)
	                  begin
	                      c_state  <= ST_READ;
	                      core_cmd <= `I2C_CMD_READ;
	                  end
	                else if (write)
	                  begin
	                      c_state  <= ST_WRITE;
	                      core_cmd <= `I2C_CMD_WRITE;
	                  end
	                else // stop
	                  begin
	                      c_state  <= ST_STOP;
	                      core_cmd <= `I2C_CMD_STOP;
	                  end

	                ld <= 1'b1;
	            end

	        ST_START:
	          if (core_ack)
	            begin
	                if (read)
	                  begin
	                      c_state  <= ST_READ;
	                      core_cmd <= `I2C_CMD_READ;
	                  end
	                else
	                  begin
	                      c_state  <= ST_WRITE;
	                      core_cmd <= `I2C_CMD_WRITE;
	                  end

	                ld <= 1'b1;
	            end

	        ST_WRITE:
	          if (core_ack)
	            if (cnt_done)
	              begin
	                  c_state  <= ST_ACK;
	                  core_cmd <= `I2C_CMD_READ;
	              end
	            else
	              begin
	                  c_state  <= ST_WRITE;       // stay in same state
	                  core_cmd <= `I2C_CMD_WRITE; // write next bit
	                  shift    <= 1'b1;
	              end

	        ST_READ:
	          if (core_ack)
	            begin
	                if (cnt_done)
	                  begin
	                      c_state  <= ST_ACK;
	                      core_cmd <= `I2C_CMD_WRITE;
	                  end
	                else
	                  begin
	                      c_state  <= ST_READ;       // stay in same state
	                      core_cmd <= `I2C_CMD_READ; // read next bit
	                  end

	                shift    <= 1'b1;
	                core_txd <= ack_in;
	            end

	        ST_ACK:
	          if (core_ack)
	            begin
	               if (stop)
	                 begin
	                     c_state  <= ST_STOP;
	                     core_cmd <= `I2C_CMD_STOP;
	                 end
	               else
	                 begin
	                     c_state  <= ST_IDLE;
	                     core_cmd <= `I2C_CMD_NOP;

	                     // generate command acknowledge signal
	                     cmd_ack  <= 1'b1;
	                 end

	                 // assign ack_out output to bit_controller_rxd (contains last received bit)
	                 ack_out <= core_rxd;

	                 core_txd <= 1'b1;
	             end
	           else
	             core_txd <= ack_in;

	        ST_STOP:
	          if (core_ack)
	            begin
	                c_state  <= ST_IDLE;
	                core_cmd <= `I2C_CMD_NOP;

	                // generate command acknowledge signal
	                cmd_ack  <= 1'b1;
	            end

	      endcase
	  end
endmodule

/////////////////////////////////////////////////////////////////////
////                                                             ////
////  WISHBONE revB.2 compliant I2C Master controller Top-level  ////
////                                                             ////
////                                                             ////
////  Author: Richard Herveille                                  ////
////          richard@asics.ws                                   ////
////          www.asics.ws                                       ////
////                                                             ////
////  Downloaded from: http://www.opencores.org/projects/i2c/    ////
////                                                             ////
/////////////////////////////////////////////////////////////////////
////                                                             ////
//// Copyright (C) 2001 Richard Herveille                        ////
////                    richard@asics.ws                         ////
////                                                             ////
//// This source file may be used and distributed without        ////
//// restriction provided that this copyright statement is not   ////
//// removed from the file and that any derivative work contains ////
//// the original copyright notice and the associated disclaimer.////
////                                                             ////
////     THIS SOFTWARE IS PROVIDED ``AS IS'' AND WITHOUT ANY     ////
//// EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED   ////
//// TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS   ////
//// FOR A PARTICULAR PURPOSE. IN NO EVENT SHALL THE AUTHOR      ////
//// OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,         ////
//// INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES    ////
//// (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE   ////
//// GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR        ////
//// BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF  ////
//// LIABILITY, WHETHER IN  CONTRACT, STRICT LIABILITY, OR TORT  ////
//// (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT  ////
//// OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE         ////
//// POSSIBILITY OF SUCH DAMAGE.                                 ////
////                                                             ////
/////////////////////////////////////////////////////////////////////

//  CVS Log
//
//  $Id: i2c_master_top.v,v 1.12 2009-01-19 20:29:26 rherveille Exp $
//
//  $Date: 2009-01-19 20:29:26 $
//  $Revision: 1.12 $
//  $Author: rherveille $
//  $Locker:  $
//  $State: Exp $
//
// Change History:
//               Revision 1.11  2005/02/27 09:26:24  rherveille
//               Fixed register overwrite issue.
//               Removed full_case pragma, replaced it by a default statement.
//
//               Revision 1.10  2003/09/01 10:34:38  rherveille
//               Fix a blocking vs. non-blocking error in the wb_dat output mux.
//
//               Revision 1.9  2003/01/09 16:44:45  rherveille
//               Fixed a bug in the Command Register declaration.
//
//               Revision 1.8  2002/12/26 16:05:12  rherveille
//               Small code simplifications
//
//               Revision 1.7  2002/12/26 15:02:32  rherveille
//               Core is now a Multimaster I2C controller
//
//               Revision 1.6  2002/11/30 22:24:40  rherveille
//               Cleaned up code
//
//               Revision 1.5  2001/11/10 10:52:55  rherveille
//               Changed PRER reset value from 0x0000 to 0xffff, conform specs.
//

// synopsys translate_off
// synopsys translate_on
module i2c_master_top(
	wb_clk_i, wb_rst_i, arst_i, wb_adr_i, wb_dat_i, wb_dat_o,
	wb_we_i, wb_stb_i, wb_cyc_i, wb_ack_o, wb_inta_o,
	scl_pad_i, scl_pad_o, scl_padoen_o, sda_pad_i, sda_pad_o, sda_padoen_o );

	// parameters
	parameter ARST_LVL = 1'b0; // asynchronous reset level

	//
	// inputs & outputs
	//

	// wishbone signals
	input        wb_clk_i;     // master clock input
	input        wb_rst_i;     // synchronous active high reset
	input        arst_i;       // asynchronous reset
	input  [2:0] wb_adr_i;     // lower address bits
	input  [7:0] wb_dat_i;     // databus input
	output [7:0] wb_dat_o;     // databus output
	input        wb_we_i;      // write enable input
	input        wb_stb_i;     // stobe/core select signal
	input        wb_cyc_i;     // valid bus cycle input
	output       wb_ack_o;     // bus cycle acknowledge output
	output       wb_inta_o;    // interrupt request signal output

	reg [7:0] wb_dat_o;
	reg wb_ack_o;
	reg wb_inta_o;

	// I2C signals
	// i2c clock line
	input  scl_pad_i;       // SCL-line input
	output scl_pad_o;       // SCL-line output (always 1'b0)
	output scl_padoen_o;    // SCL-line output enable (active low)

	// i2c data line
	input  sda_pad_i;       // SDA-line input
	output sda_pad_o;       // SDA-line output (always 1'b0)
	output sda_padoen_o;    // SDA-line output enable (active low)


	//
	// variable declarations
	//

	// registers
	reg  [15:0] prer; // clock prescale register
	reg  [ 7:0] ctr;  // control register
	reg  [ 7:0] txr;  // transmit register
	wire [ 7:0] rxr;  // receive register
	reg  [ 7:0] cr;   // command register
	wire [ 7:0] sr;   // status register

	// done signal: command completed, clear command register
	wire done;

	// core enable signal
	wire core_en;
	wire ien;

	// status register signals
	wire irxack;
	reg  rxack;       // received aknowledge from slave
	reg  tip;         // transfer in progress
	reg  irq_flag;    // interrupt pending flag
	wire i2c_busy;    // bus busy (start signal detected)
	wire i2c_al;      // i2c bus arbitration lost
	reg  al;          // status register arbitration lost bit

	//
	// module body
	//

	// generate internal reset
	wire rst_i = arst_i ^ ARST_LVL;

	// generate wishbone signals
	wire wb_wacc = wb_we_i & wb_ack_o;

	// generate acknowledge output signal
	always @(posedge wb_clk_i)
	  wb_ack_o <= wb_cyc_i & wb_stb_i & ~wb_ack_o; // because timing is always honored

	// assign DAT_O
	always @(posedge wb_clk_i)
	begin
	  case (wb_adr_i) // synopsys parallel_case
	    3'b000: wb_dat_o <= prer[ 7:0];
	    3'b001: wb_dat_o <= prer[15:8];
	    3'b010: wb_dat_o <= ctr;
	    3'b011: wb_dat_o <= rxr; // write is transmit register (txr)
	    3'b100: wb_dat_o <= sr;  // write is command register (cr)
	    3'b101: wb_dat_o <= txr;
	    3'b110: wb_dat_o <= cr;
	    3'b111: wb_dat_o <= 0;   // reserved
	  endcase
	end

	// generate registers
	always @(posedge wb_clk_i or negedge rst_i)
	  if (!rst_i)
	    begin
	        prer <= 16'hffff;
	        ctr  <= 8'h0;
	        txr  <= 8'h0;
	    end
	  else if (wb_rst_i)
	    begin
	        prer <= 16'hffff;
	        ctr  <= 8'h0;
	        txr  <= 8'h0;
	    end
	  else
	    if (wb_wacc)
	      case (wb_adr_i) // synopsys parallel_case
	         3'b000 : prer [ 7:0] <= wb_dat_i;
	         3'b001 : prer [15:8] <= wb_dat_i;
	         3'b010 : ctr         <= wb_dat_i;
	         3'b011 : txr         <= wb_dat_i;
	         default: ;
	      endcase

	// generate command register (special case)
	always @(posedge wb_clk_i or negedge rst_i)
	  if (!rst_i)
	    cr <= 8'h0;
	  else if (wb_rst_i)
	    cr <= 8'h0;
	  else if (wb_wacc)
	    begin
	        if (core_en & (wb_adr_i == 3'b100) )
	          cr <= wb_dat_i;
	    end
	  else
	    begin
	        if (done | i2c_al)
	          cr[7:4] <= 4'h0;           // clear command bits when done
	                                        // or when aribitration lost
	        cr[2:1] <= 2'b0;             // reserved bits
	        cr[0]   <= 1'b0;             // clear IRQ_ACK bit
	    end


	// decode command register
	wire sta  = cr[7];
	wire sto  = cr[6];
	wire rd   = cr[5];
	wire wr   = cr[4];
	wire ack  = cr[3];
	wire iack = cr[0];

	// decode control register
	assign core_en = ctr[7];
	assign ien = ctr[6];

	// hookup byte controller block
	i2c_master_byte_ctrl byte_controller (
		.clk      ( wb_clk_i     ),
		.rst      ( wb_rst_i     ),
		.nReset   ( rst_i        ),
		.ena      ( core_en      ),
		.clk_cnt  ( prer         ),
		.start    ( sta          ),
		.stop     ( sto          ),
		.read     ( rd           ),
		.write    ( wr           ),
		.ack_in   ( ack          ),
		.din      ( txr          ),
		.cmd_ack  ( done         ),
		.ack_out  ( irxack       ),
		.dout     ( rxr          ),
		.i2c_busy ( i2c_busy     ),
		.i2c_al   ( i2c_al       ),
		.scl_i    ( scl_pad_i    ),
		.scl_o    ( scl_pad_o    ),
		.scl_oen  ( scl_padoen_o ),
		.sda_i    ( sda_pad_i    ),
		.sda_o    ( sda_pad_o    ),
		.sda_oen  ( sda_padoen_o )
	);

	// status register block + interrupt request signal
	always @(posedge wb_clk_i or negedge rst_i)
	  if (!rst_i)
	    begin
	        al       <= 1'b0;
	        rxack    <= 1'b0;
	        tip      <= 1'b0;
	        irq_flag <= 1'b0;
	    end
	  else if (wb_rst_i)
	    begin
	        al       <= 1'b0;
	        rxack    <= 1'b0;
	        tip      <= 1'b0;
	        irq_flag <= 1'b0;
	    end
	  else
	    begin
	        al       <= i2c_al | (al & ~sta);
	        rxack    <= irxack;
	        tip      <= (rd | wr);
	        irq_flag <= (done | i2c_al | irq_flag) & ~iack; // interrupt request flag is always generated
	    end

	// generate interrupt request signals
	always @(posedge wb_clk_i or negedge rst_i)
	  if (!rst_i)
	    wb_inta_o <= 1'b0;
	  else if (wb_rst_i)
	    wb_inta_o <= 1'b0;
	  else
	    wb_inta_o <= irq_flag && ien; // interrupt signal is only generated when IEN (interrupt enable bit is set)

	// assign status register bits
	assign sr[7]   = rxack;
	assign sr[6]   = i2c_busy;
	assign sr[5]   = al;
	assign sr[4:2] = 3'h0; // reserved
	assign sr[1]   = tip;
	assign sr[0]   = irq_flag;

endmodule

// ---------------------------------------------------------------------------
// In-house (rtl-env): an I2C target (slave) model and the system top.
// Copyright 2026 Swarchis Kulkarni and Noel Thomas.
// ---------------------------------------------------------------------------

// A register target: 4 x 8-bit registers behind a 2-bit pointer.
// Write: [ADDR+W] [pointer] [data] [data] ...   (each data byte goes to mem[ptr], ptr+1)
// Read:  [ADDR+R] [data] [data] ...             (sends mem[ptr], ptr+1 after each ACKed byte)
module i2c_target #(
    parameter [6:0] ADDR    = 7'h50,
    parameter       STRETCH = 24
) (
    input  wire clk,
    input  wire rst_n,
    input  wire scl,          // bus lines
    input  wire sda,
    input  wire stretch_en,   // 1: hold SCL low for STRETCH cycles after each byte's ACK slot
    output reg  scl_low,      // 1: pull SCL low
    output reg  sda_low       // 1: pull SDA low
);
    localparam [2:0] IDLE = 3'd0, ADR = 3'd1, WPTR = 3'd2, WDAT = 3'd3, RDAT = 3'd4;

    reg       scl_d, sda_d;
    reg [2:0] state;
    reg [3:0] bitcnt;
    reg [7:0] shreg;
    reg [1:0] ptr;
    reg       rw;
    reg       nack;
    reg [7:0] mem [0:3];
    reg [7:0] stretch_cnt;

    wire start    = scl & scl_d & sda_d & ~sda;
    wire stop     = scl & scl_d & ~sda_d & sda;
    wire scl_rise = scl & ~scl_d;
    wire scl_fall = ~scl & scl_d;

    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            scl_d <= 1'b1;
            sda_d <= 1'b1;
        end else begin
            scl_d <= scl;
            sda_d <= sda;
        end

    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            state       <= IDLE;
            bitcnt      <= 4'd0;
            shreg       <= 8'h00;
            ptr         <= 2'd0;
            rw          <= 1'b0;
            nack        <= 1'b0;
            sda_low     <= 1'b0;
            scl_low     <= 1'b0;
            stretch_cnt <= 8'd0;
            mem[0]      <= 8'h00;
            mem[1]      <= 8'h00;
            mem[2]      <= 8'h00;
            mem[3]      <= 8'h00;
        end else begin
            if (stretch_cnt != 8'd0) begin
                stretch_cnt <= stretch_cnt - 8'd1;
                if (stretch_cnt == 8'd1)
                    scl_low <= 1'b0;
            end
            if (start) begin
                state   <= ADR;
                bitcnt  <= 4'd0;
                sda_low <= 1'b0;
            end else if (stop) begin
                state   <= IDLE;
                sda_low <= 1'b0;
            end else if (state == RDAT) begin
                if (scl_rise) begin
                    if (bitcnt < 4'd8)
                        bitcnt <= bitcnt + 4'd1;
                    else if (bitcnt == 4'd8) begin
                        nack   <= sda;
                        bitcnt <= 4'd9;
                    end
                end else if (scl_fall) begin
                    if (bitcnt >= 4'd1 && bitcnt <= 4'd7)
                        sda_low <= ~shreg[4'd7 - bitcnt];
                    else if (bitcnt == 4'd8)
                        sda_low <= 1'b0;
                    else if (bitcnt == 4'd9) begin
                        if (stretch_en) begin
                            scl_low     <= 1'b1;
                            stretch_cnt <= STRETCH;
                        end
                        if (nack) begin
                            state   <= IDLE;
                            sda_low <= 1'b0;
                        end else begin
                            ptr     <= ptr + 2'd1;
                            shreg   <= mem[ptr + 2'd1];
                            sda_low <= ~mem[ptr + 2'd1][7];
                            bitcnt  <= 4'd0;
                        end
                    end
                end
            end else if (state != IDLE) begin
                if (scl_rise && bitcnt < 4'd8) begin
                    shreg  <= {shreg[6:0], sda};
                    bitcnt <= bitcnt + 4'd1;
                end else if (scl_fall && bitcnt == 4'd8) begin
                    bitcnt <= 4'd9;
                    case (state)
                        ADR:
                            if (shreg[7:1] == ADDR) begin
                                sda_low <= 1'b1;
                                rw      <= shreg[0];
                            end else
                                state   <= IDLE;
                        WPTR: begin
                            ptr     <= shreg[1:0];
                            sda_low <= 1'b1;
                        end
                        default: begin
                            mem[ptr] <= shreg;
                            ptr      <= ptr + 2'd1;
                            sda_low  <= 1'b1;
                        end
                    endcase
                end else if (scl_fall && bitcnt == 4'd9) begin
                    bitcnt <= 4'd0;
                    if (stretch_en) begin
                        scl_low     <= 1'b1;
                        stretch_cnt <= STRETCH;
                    end
                    if (state == ADR && rw) begin
                        state   <= RDAT;
                        shreg   <= mem[ptr];
                        sda_low <= ~mem[ptr][7];
                    end else begin
                        sda_low <= 1'b0;
                        if (state == ADR)
                            state <= WPTR;
                        else
                            state <= WDAT;
                    end
                end
            end
        end
endmodule

// The system: the OpenCores I2C master and the target on one open-drain bus with pull-ups.
module i2c_sys (
    input  wire       clk,
    input  wire       rst_n,        // asynchronous reset, active low (master and target)
    input  wire       wb_rst_i,     // synchronous reset of the master only, active high
    input  wire [2:0] wb_adr_i,
    input  wire [7:0] wb_dat_i,
    output wire [7:0] wb_dat_o,
    input  wire       wb_we_i,
    input  wire       wb_stb_i,
    input  wire       wb_cyc_i,
    output wire       wb_ack_o,
    output wire       wb_inta_o,
    input  wire       stretch_en,   // target clock stretching
    input  wire       ext_scl_low,  // another device pulling SCL low
    input  wire       ext_sda_low,  // another device pulling SDA low
    output wire       scl,          // bus lines
    output wire       sda
);
    wire scl_pad_o, scl_padoen_o, sda_pad_o, sda_padoen_o;
    wire tgt_scl_low, tgt_sda_low;

    assign scl = (scl_padoen_o | scl_pad_o) & ~tgt_scl_low & ~ext_scl_low;
    assign sda = (sda_padoen_o | sda_pad_o) & ~tgt_sda_low & ~ext_sda_low;

    i2c_master_top #(.ARST_LVL(1'b0)) u_master (
        .wb_clk_i(clk), .wb_rst_i(wb_rst_i), .arst_i(rst_n),
        .wb_adr_i(wb_adr_i), .wb_dat_i(wb_dat_i), .wb_dat_o(wb_dat_o),
        .wb_we_i(wb_we_i), .wb_stb_i(wb_stb_i), .wb_cyc_i(wb_cyc_i),
        .wb_ack_o(wb_ack_o), .wb_inta_o(wb_inta_o),
        .scl_pad_i(scl), .scl_pad_o(scl_pad_o), .scl_padoen_o(scl_padoen_o),
        .sda_pad_i(sda), .sda_pad_o(sda_pad_o), .sda_padoen_o(sda_padoen_o)
    );

    i2c_target #(.ADDR(7'h50), .STRETCH(24)) u_target (
        .clk(clk), .rst_n(rst_n), .scl(scl), .sda(sda), .stretch_en(stretch_en),
        .scl_low(tgt_scl_low), .sda_low(tgt_sda_low)
    );
endmodule
