A5 Project)
Assessing the OpenTelemetry (OTel) capabilities for mixedcriticality workloads.Monitoring is a crucial step of a system operation, especially in real-time scenarios. During this process, an important step is to identify the monitoring data to produce, employing the proper frameworks and technologies to gather and analyze the system while maintaining its nominal operational behavior. The OpenTelemetry framework has been widely adopted in recent years due to its versatility and applicability in several steps of the monitoring pipeline, from the production of monitoring data to its collection, processing and visualization. Mixed-criticality systems are characterized by a collection of real-time tasks with different criticalities/priorities. Monitoring infrastructures adopted for these platforms should be able to prioritize critical tasks over low-criticality or best-effort ones.The students are asked to set up a simple mixed-criticality workload to execute and monitor. Students should be able to assess the already available OTel capabilities (e.g., tracing volume, sampling rate, etc.) to prioritize high-critical tasks. Students should also evaluate eventual monitoring overhead on the WCET. Suggestion: OTel does not support the C language; students can write their real-time tasks in any of the languages supported by the Framework (check the official documentation). Rust is supported and can be employed to leverage both the libc and OTel libraries. When developing mixed-criticality workloads, students can reproduce or instrument open-source tasks employed in the state-of-the-art or create their own (keep in mind that the main focus of this project is to evaluate the OTel capabilities for mixed-criticality systems, and to develop a complex mixed-criticality task set).

Tasks for Students:
Students should set up an experimental campaign to provide evidence of actual task prioritization. If the results of such evaluation should provide evidence of inefficient or non-existent OTel capabilities, students should analyze the OTel codebase to identify potential improvements to its architecture.

What the Students Will Learn:

- Dealing with real, state-of-art monitoring tools and real-time tasks andconstraints.
- Understanding of the impact of monitoring overhead on SLOs compliance.
- Understanding the different configurations of the OpenTelemetry MonitoringFramework in the context of mixed-criticality systems.

Required Tools:

- A real-time OS (e.g., PREEMPT-RT Linux), deployed as bare-metal.
- RT-POSIX
- OpenTelemetry Framework.

Expected Deliverables:

- Source code and configuration files.
- Students are invited to report any kind of data to support their experimentalcampaign.
- Remember to maintain statistical relevance when acquiring any kind ofmeasurement, performing experiments 10-30 times. This ensures thatexperimental evaluations are not misled by stochastic events.

Team size: 1-2 person/people Duration: 4 weeks