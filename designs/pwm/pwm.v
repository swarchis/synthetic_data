module pwm #(
  parameter WIDTH = 8
) (
  input              clk,
  input              rst,
  input              en,
  input  [WIDTH-1:0] period,
  input  [WIDTH-1:0] duty,
  output [WIDTH-1:0] cnt,
  output             out,
  output             wrap
);
  reg [WIDTH-1:0] count;

  wire last = (period == 0) || (count >= period - 1);

  always @(posedge clk) begin
    if (rst || !en)
      count <= 0;
    else if (last)
      count <= 0;
    else
      count <= count + 1;
  end

  assign cnt  = count;
  assign out  = en && (count < duty);
  assign wrap = en && last;
endmodule
