module rr_arbiter #(
  parameter N = 4
) (
  input                      clk,
  input                      rst,
  input      [N-1:0]         req,
  output reg [N-1:0]         grant,
  output     [$clog2(N)-1:0] last_idx
);
  reg [$clog2(N)-1:0] last;
  reg [$clog2(N)-1:0] pick;
  reg                 found;
  integer k;

  // Search starting just after the last winner, wrapping around.
  always @(*) begin
    found = 1'b0;
    pick  = 0;
    for (k = 1; k <= N; k = k + 1)
      if (!found && req[(last + k) % N]) begin
        found = 1'b1;
        pick  = (last + k) % N;
      end
  end

  always @(posedge clk) begin
    if (rst) begin
      grant <= 0;
      last  <= N - 1;
    end else if (found) begin
      grant <= 1 << pick;
      last  <= pick;
    end else
      grant <= 0;
  end

  assign last_idx = last;
endmodule
