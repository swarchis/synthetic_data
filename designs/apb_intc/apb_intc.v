// APB interrupt controller: NUM_SRC sources, PRIO_BITS of priority each,
// claim/complete handshake. See spec.md for the full register map.

module intc_apb_if (
  input         PSEL,
  input         PENABLE,
  input         PWRITE,
  input  [7:0]  PADDR,
  input  [31:0] PWDATA,
  output [31:0] PRDATA,
  output        PREADY,
  output        PSLVERR,
  output        wr_en,
  output        rd_en,
  output [7:0]  addr,
  output [31:0] wdata,
  input  [31:0] rdata,
  input         err
);
  wire access = PSEL && PENABLE;

  assign wr_en   = access && PWRITE;
  assign rd_en   = access && !PWRITE;
  assign addr    = PADDR;
  assign wdata   = PWDATA;
  assign PREADY  = 1'b1;
  assign PSLVERR = access && err;
  assign PRDATA  = (rd_en && !err) ? rdata : 32'b0;
endmodule


module intc_sync #(
  parameter N = 8
) (
  input              clk,
  input              rst,
  input      [N-1:0] d,
  output reg [N-1:0] q
);
  reg [N-1:0] meta;

  always @(posedge clk) begin
    if (rst) begin
      meta <= 0;
      q    <= 0;
    end else begin
      meta <= d;
      q    <= meta;
    end
  end
endmodule


module intc_pending #(
  parameter N = 8
) (
  input              clk,
  input              rst,
  input      [N-1:0] src,
  input      [N-1:0] trigger,
  input      [N-1:0] clear_mask,
  input      [N-1:0] force_mask,
  output reg [N-1:0] pending
);
  reg [N-1:0] src_prev;

  wire [N-1:0] rise = src & ~src_prev;
  wire [N-1:0] set  = (trigger & rise) | (~trigger & src) | force_mask;

  always @(posedge clk) begin
    if (rst) begin
      src_prev <= 0;
      pending  <= 0;
    end else begin
      src_prev <= src;
      pending  <= (pending & ~clear_mask) | set;
    end
  end
endmodule


module intc_prio_select #(
  parameter N         = 8,
  parameter PRIO_BITS = 3
) (
  input      [N-1:0]           eligible,
  input      [N*PRIO_BITS-1:0] prio_flat,
  output reg                   best_valid,
  output reg [$clog2(N)-1:0]   best_idx,
  output reg [PRIO_BITS-1:0]   best_prio
);
  integer k;

  // Highest priority wins; strict > keeps the lowest index on a tie and
  // never selects a priority-0 source.
  always @(*) begin
    best_valid = 1'b0;
    best_idx   = 0;
    best_prio  = 0;
    for (k = 0; k < N; k = k + 1)
      if (eligible[k] && prio_flat[k*PRIO_BITS +: PRIO_BITS] > best_prio) begin
        best_valid = 1'b1;
        best_idx   = k;
        best_prio  = prio_flat[k*PRIO_BITS +: PRIO_BITS];
      end
  end
endmodule


module intc_regs #(
  parameter NUM_SRC   = 8,
  parameter PRIO_BITS = 3
) (
  input                              clk,
  input                              rst,
  input                              wr_en,
  input                              rd_en,
  input      [7:0]                   addr,
  input      [31:0]                  wdata,
  output reg [31:0]                  rdata,
  output                             err,
  input      [NUM_SRC-1:0]           pending,
  input                              irq,
  input      [$clog2(NUM_SRC+1)-1:0] irq_id,
  output reg                         gen,
  output reg [NUM_SRC-1:0]           enable,
  output reg [NUM_SRC-1:0]           trigger,
  output reg [NUM_SRC-1:0]           in_service,
  output reg [PRIO_BITS-1:0]         threshold,
  output reg [NUM_SRC*PRIO_BITS-1:0] prio_flat,
  output     [NUM_SRC-1:0]           clear_mask,
  output     [NUM_SRC-1:0]           force_mask
);
  localparam A_CTRL      = 8'h00;
  localparam A_ENABLE    = 8'h04;
  localparam A_TRIGGER   = 8'h08;
  localparam A_PENDING   = 8'h0C;
  localparam A_FORCE     = 8'h10;
  localparam A_THRESH    = 8'h14;
  localparam A_CLAIM     = 8'h18;
  localparam A_INSERVICE = 8'h1C;
  localparam A_STATUS    = 8'h20;
  localparam A_ID        = 8'h24;
  localparam A_PRIO0     = 8'h40;
  localparam PRIO_WORDS  = (NUM_SRC + 7) / 8;
  localparam [31:0] ID_VALUE = 32'h1C0D0000 | (PRIO_BITS << 8) | NUM_SRC;

  reg mapped, read_only;
  integer j;

  wire       is_prio   = (addr >= A_PRIO0) && (addr < A_PRIO0 + 4 * PRIO_WORDS);
  wire [7:0] prio_word = (addr - A_PRIO0) >> 2;

  always @(*) begin
    mapped    = is_prio;
    read_only = 1'b0;
    case (addr)
      A_CTRL, A_ENABLE, A_TRIGGER, A_PENDING, A_FORCE, A_THRESH, A_CLAIM:
        mapped = 1'b1;
      A_INSERVICE, A_STATUS, A_ID: begin
        mapped    = 1'b1;
        read_only = 1'b1;
      end
      default: ;
    endcase
  end

  assign err = (addr[1:0] != 2'b00) || !mapped || (wr_en && read_only);

  wire do_write = wr_en && !err;
  wire claim    = rd_en && !err && (addr == A_CLAIM) && irq;

  assign clear_mask = (do_write && addr == A_PENDING ? wdata[NUM_SRC-1:0] : {NUM_SRC{1'b0}})
                    | (claim ? ({{(NUM_SRC-1){1'b0}}, 1'b1} << (irq_id - 1)) : {NUM_SRC{1'b0}});
  assign force_mask = (do_write && addr == A_FORCE) ? wdata[NUM_SRC-1:0] : {NUM_SRC{1'b0}};

  always @(*) begin
    rdata = 32'b0;
    case (addr)
      A_CTRL:      rdata[0] = gen;
      A_ENABLE:    rdata[NUM_SRC-1:0] = enable;
      A_TRIGGER:   rdata[NUM_SRC-1:0] = trigger;
      A_PENDING:   rdata[NUM_SRC-1:0] = pending;
      A_THRESH:    rdata[PRIO_BITS-1:0] = threshold;
      A_CLAIM:     rdata = irq ? irq_id : 32'b0;
      A_INSERVICE: rdata[NUM_SRC-1:0] = in_service;
      A_STATUS: begin
        rdata[0]    = irq;
        rdata[15:8] = irq ? irq_id : 8'b0;
      end
      A_ID:        rdata = ID_VALUE;
      default:
        if (is_prio)
          for (j = 0; j < 8; j = j + 1)
            if (prio_word * 8 + j < NUM_SRC)
              rdata[4*j +: PRIO_BITS] = prio_flat[(prio_word*8 + j)*PRIO_BITS +: PRIO_BITS];
    endcase
  end

  always @(posedge clk) begin
    if (rst) begin
      gen        <= 1'b0;
      enable     <= 0;
      trigger    <= 0;
      in_service <= 0;
      threshold  <= 0;
      prio_flat  <= 0;
    end else begin
      if (claim)
        in_service[irq_id - 1] <= 1'b1;
      if (do_write) begin
        case (addr)
          A_CTRL:    gen       <= wdata[0];
          A_ENABLE:  enable    <= wdata[NUM_SRC-1:0];
          A_TRIGGER: trigger   <= wdata[NUM_SRC-1:0];
          A_THRESH:  threshold <= wdata[PRIO_BITS-1:0];
          A_CLAIM:
            if (wdata >= 1 && wdata <= NUM_SRC)
              in_service[wdata - 1] <= 1'b0;
          default:
            if (is_prio)
              for (j = 0; j < 8; j = j + 1)
                if (prio_word * 8 + j < NUM_SRC)
                  prio_flat[(prio_word*8 + j)*PRIO_BITS +: PRIO_BITS] <= wdata[4*j +: PRIO_BITS];
        endcase
      end
    end
  end
endmodule


module apb_intc #(
  parameter NUM_SRC   = 8,
  parameter PRIO_BITS = 3
) (
  input                              PCLK,
  input                              PRESETn,
  input                              PSEL,
  input                              PENABLE,
  input                              PWRITE,
  input  [7:0]                       PADDR,
  input  [31:0]                      PWDATA,
  output [31:0]                      PRDATA,
  output                             PREADY,
  output                             PSLVERR,
  input  [NUM_SRC-1:0]               irq_src,
  output                             irq,
  output [$clog2(NUM_SRC+1)-1:0]     irq_id
);
  localparam IDW = $clog2(NUM_SRC + 1);

  wire                     rst = !PRESETn;
  wire                     wr_en, rd_en, err;
  wire [7:0]               addr;
  wire [31:0]              wdata, rdata;
  wire                     gen;
  wire [NUM_SRC-1:0]       enable, trigger, in_service, pending, src_sync, clear_mask, force_mask;
  wire [PRIO_BITS-1:0]     threshold, best_prio;
  wire [NUM_SRC*PRIO_BITS-1:0] prio_flat;
  wire                     best_valid;
  wire [$clog2(NUM_SRC)-1:0] best_idx;

  wire eligible_any = best_valid && gen && (best_prio > threshold);
  wire [NUM_SRC-1:0] eligible = pending & enable & ~in_service;

  assign irq    = eligible_any;
  assign irq_id = irq ? best_idx + 1 : {IDW{1'b0}};

  intc_apb_if u_apb (
    .PSEL(PSEL), .PENABLE(PENABLE), .PWRITE(PWRITE), .PADDR(PADDR), .PWDATA(PWDATA),
    .PRDATA(PRDATA), .PREADY(PREADY), .PSLVERR(PSLVERR),
    .wr_en(wr_en), .rd_en(rd_en), .addr(addr), .wdata(wdata), .rdata(rdata), .err(err)
  );

  intc_sync #(.N(NUM_SRC)) u_sync (
    .clk(PCLK), .rst(rst), .d(irq_src), .q(src_sync)
  );

  intc_pending #(.N(NUM_SRC)) u_pending (
    .clk(PCLK), .rst(rst), .src(src_sync), .trigger(trigger),
    .clear_mask(clear_mask), .force_mask(force_mask), .pending(pending)
  );

  intc_prio_select #(.N(NUM_SRC), .PRIO_BITS(PRIO_BITS)) u_select (
    .eligible(eligible), .prio_flat(prio_flat),
    .best_valid(best_valid), .best_idx(best_idx), .best_prio(best_prio)
  );

  intc_regs #(.NUM_SRC(NUM_SRC), .PRIO_BITS(PRIO_BITS)) u_regs (
    .clk(PCLK), .rst(rst), .wr_en(wr_en), .rd_en(rd_en), .addr(addr), .wdata(wdata),
    .rdata(rdata), .err(err), .pending(pending), .irq(irq), .irq_id(irq_id),
    .gen(gen), .enable(enable), .trigger(trigger), .in_service(in_service),
    .threshold(threshold), .prio_flat(prio_flat),
    .clear_mask(clear_mask), .force_mask(force_mask)
  );
endmodule
