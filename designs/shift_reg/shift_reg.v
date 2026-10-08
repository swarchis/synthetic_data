module shift_reg #(
  parameter WIDTH = 8
) (
  input                  clk,
  input                  rst,
  input                  load,
  input      [WIDTH-1:0] din,
  input                  shift_en,
  input                  dir,
  input                  sin,
  output reg [WIDTH-1:0] q,
  output                 sout
);
  always @(posedge clk) begin
    if (rst)
      q <= 0;
    else if (load)
      q <= din;
    else if (shift_en) begin
      if (dir)
        q <= {sin, q[WIDTH-1:1]};
      else
        q <= {q[WIDTH-2:0], sin};
    end
  end

  assign sout = dir ? q[0] : q[WIDTH-1];
endmodule
