module priority_encoder #(
  parameter N = 8
) (
  input      [N-1:0]         req,
  output reg [$clog2(N)-1:0] idx,
  output                     valid,
  output     [N-1:0]         onehot
);
  integer i;

  always @(*) begin
    idx = 0;
    for (i = 0; i < N; i = i + 1)
      if (req[i])
        idx = i;
  end

  assign valid  = |req;
  assign onehot = valid ? (1 << idx) : 0;
endmodule
