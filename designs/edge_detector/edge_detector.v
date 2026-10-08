module edge_detector (
  input      clk,
  input      rst,
  input      sig,
  output reg rise,
  output reg fall,
  output     any_edge
);
  reg sig_d;

  always @(posedge clk) begin
    if (rst) begin
      sig_d <= 1'b0;
      rise  <= 1'b0;
      fall  <= 1'b0;
    end else begin
      sig_d <= sig;
      rise  <= sig & ~sig_d;
      fall  <= ~sig & sig_d;
    end
  end

  assign any_edge = rise | fall;
endmodule
