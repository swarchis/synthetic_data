module fifo #(
  parameter WIDTH = 8,
  parameter DEPTH = 4
) (
  input                       clk,
  input                       rst,
  input                       push,
  input      [WIDTH-1:0]      din,
  input                       pop,
  output     [WIDTH-1:0]      dout,
  output                      full,
  output                      empty,
  output     [$clog2(DEPTH):0] count
);
  localparam AW = $clog2(DEPTH);

  reg [WIDTH-1:0] mem [0:DEPTH-1];
  reg [AW:0] wr_ptr, rd_ptr;

  wire do_push = push && !full;
  wire do_pop  = pop && !empty;

  always @(posedge clk) begin
    if (rst) begin
      wr_ptr <= 0;
      rd_ptr <= 0;
    end else begin
      if (do_push) begin
        mem[wr_ptr[AW-1:0]] <= din;
        wr_ptr <= wr_ptr + 1;
      end
      if (do_pop)
        rd_ptr <= rd_ptr + 1;
    end
  end

  assign dout  = mem[rd_ptr[AW-1:0]];
  assign count = wr_ptr - rd_ptr;
  assign empty = (wr_ptr == rd_ptr);
  assign full  = (wr_ptr[AW] != rd_ptr[AW]) && (wr_ptr[AW-1:0] == rd_ptr[AW-1:0]);
endmodule
