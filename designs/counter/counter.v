module counter (input clk, input rst, output reg [3:0] count);
  always @(posedge clk)
    if (rst) count <= 0;
    else     count <= count + 1;
endmodule
