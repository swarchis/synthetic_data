module uart_tx #(
  parameter CLKS_PER_BIT = 4
) (
  input       clk,
  input       rst,
  input       start,
  input [7:0] data,
  output reg  tx,
  output      busy,
  output reg  done
);
  localparam S_IDLE  = 2'd0;
  localparam S_START = 2'd1;
  localparam S_DATA  = 2'd2;
  localparam S_STOP  = 2'd3;

  reg [1:0] state;
  reg [$clog2(CLKS_PER_BIT)-1:0] clk_cnt;
  reg [2:0] bit_idx;
  reg [7:0] shreg;

  always @(posedge clk) begin
    if (rst) begin
      state   <= S_IDLE;
      tx      <= 1'b1;
      clk_cnt <= 0;
      bit_idx <= 0;
      shreg   <= 0;
      done    <= 1'b0;
    end else begin
      done <= 1'b0;
      case (state)
        S_IDLE: begin
          tx <= 1'b1;
          if (start) begin
            shreg   <= data;
            state   <= S_START;
            tx      <= 1'b0;
            clk_cnt <= 0;
          end
        end
        S_START:
          if (clk_cnt == CLKS_PER_BIT - 1) begin
            clk_cnt <= 0;
            bit_idx <= 0;
            state   <= S_DATA;
            tx      <= shreg[0];
          end else
            clk_cnt <= clk_cnt + 1;
        S_DATA:
          if (clk_cnt == CLKS_PER_BIT - 1) begin
            clk_cnt <= 0;
            if (bit_idx == 7) begin
              state <= S_STOP;
              tx    <= 1'b1;
            end else begin
              bit_idx <= bit_idx + 1;
              tx      <= shreg[bit_idx + 1];
            end
          end else
            clk_cnt <= clk_cnt + 1;
        S_STOP:
          if (clk_cnt == CLKS_PER_BIT - 1) begin
            clk_cnt <= 0;
            state   <= S_IDLE;
            done    <= 1'b1;
          end else
            clk_cnt <= clk_cnt + 1;
      endcase
    end
  end

  assign busy = (state != S_IDLE);
endmodule
