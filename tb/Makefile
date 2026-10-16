SIM ?= verilator
TOPLEVEL_LANG ?= verilog
RTL := $(abspath ../rtl)
SVA_DIR := $(abspath ../sva)
# SVA=1 (default): compile the bound SystemVerilog assertions with --assert.
SVA ?= 1
VERILOG_SOURCES ?= $(RTL)/periph_soc.sv $(RTL)/apb_uart.sv $(RTL)/apb_timer.sv $(RTL)/apb_gpio.sv
ifeq ($(SVA),1)
VERILOG_SOURCES += $(SVA_DIR)/soc_sva.sv $(SVA_DIR)/sva_bind.sv
EXTRA_ARGS += --assert
endif
TOPLEVEL := periph_soc
MODULE := test_soc
EXTRA_ARGS += -Wno-fatal $(BUG_DEFINES)
include $(shell cocotb-config --makefiles)/Makefile.sim
