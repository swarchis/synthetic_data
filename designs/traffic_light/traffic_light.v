module traffic_light #(
  parameter GREEN_TIME  = 8,
  parameter MIN_GREEN   = 3,
  parameter YELLOW_TIME = 3,
  parameter RED_TIME    = 6
) (
  input            clk,
  input            rst,
  input            ped,
  output reg [1:0] state,
  output           green,
  output           yellow,
  output           red,
  output           walk
);
  localparam S_GREEN  = 2'd0;
  localparam S_YELLOW = 2'd1;
  localparam S_RED    = 2'd2;

  reg [3:0] timer;
  reg       ped_pending;

  wire green_done = (timer == GREEN_TIME - 1) || (ped_pending && timer >= MIN_GREEN - 1);

  always @(posedge clk) begin
    if (rst) begin
      state       <= S_GREEN;
      timer       <= 0;
      ped_pending <= 1'b0;
    end else begin
      if (state == S_RED)
        ped_pending <= 1'b0;
      else if (ped)
        ped_pending <= 1'b1;

      case (state)
        S_GREEN:
          if (green_done) begin
            state <= S_YELLOW;
            timer <= 0;
          end else
            timer <= timer + 1;
        S_YELLOW:
          if (timer == YELLOW_TIME - 1) begin
            state <= S_RED;
            timer <= 0;
          end else
            timer <= timer + 1;
        S_RED:
          if (timer == RED_TIME - 1) begin
            state <= S_GREEN;
            timer <= 0;
          end else
            timer <= timer + 1;
        default: begin
          state <= S_GREEN;
          timer <= 0;
        end
      endcase
    end
  end

  assign green  = (state == S_GREEN);
  assign yellow = (state == S_YELLOW);
  assign red    = (state == S_RED);
  assign walk   = red;
endmodule
