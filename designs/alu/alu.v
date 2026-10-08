module alu #(
  parameter WIDTH = 8
) (
  input      [WIDTH-1:0] a,
  input      [WIDTH-1:0] b,
  input      [2:0]       op,
  output reg [WIDTH-1:0] y,
  output reg             carry,
  output                 zero
);
  always @(*) begin
    carry = 1'b0;
    case (op)
      3'd0: {carry, y} = a + b;
      3'd1: {carry, y} = a - b;
      3'd2: y = a & b;
      3'd3: y = a | b;
      3'd4: y = a ^ b;
      3'd5: begin
        y = a << 1;
        carry = a[WIDTH-1];
      end
      3'd6: begin
        y = a >> 1;
        carry = a[0];
      end
      3'd7: y = (a < b) ? 1 : 0;
      default: y = 0;
    endcase
  end

  assign zero = (y == 0);
endmodule
