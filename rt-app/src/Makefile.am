ACLOCAL_AMFLAGS = -I m4
# 1. Preprocessor Flags: Added OTel include path
AM_CPPFLAGS = -I$(srcdir)/../libdl/ \
              -I$(top_srcdir)/../otel-installdir/include/

# 2. Compiler Flags: Enforce C++17 (required by OTel)
AM_CXXFLAGS = -std=c++17

bin_PROGRAMS = rt-app

rt_app_SOURCES= rt-app_types.h rt-app_args.h rt-app_utils.h rt-app_utils.cpp rt-app_args.cpp rt-app.h  rt-app.cpp 
rt_app_SOURCES += rt-app_parse_config.h rt-app_parse_config.cpp rt-app_taskgroups.h rt-app_taskgroups.cpp

# 3. Linker Flags: Added OTel library path and specific libraries
rt_app_LDADD = $(QRESLIB) \
               -L$(top_srcdir)/../otel-installdir/lib \
               -lopentelemetry_exporter_zipkin_trace \
               -lopentelemetry_exporter_ostream_span \
               -lopentelemetry_exporter_ostream_span_builder \
               -lopentelemetry_http_client_curl \
               -lopentelemetry_trace \
               -lopentelemetry_resources \
               -lopentelemetry_common \
               -lcurl \
               -lpthread


if SET_DLSCHED
rt_app_LDADD += ../libdl/libdl.a
endif

dist_bin_SCRIPTS = $(srcdir)/../doc/workgen
